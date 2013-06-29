import sublime
import sublime_plugin

import sys
import threading
import os.path
import codecs
import time
import json

import lsutils.editor as eutils

is_python3 = sys.version_info[0] > 2
BASE_PATH = os.path.abspath(os.path.dirname(__file__))
PACKAGES_PATH = sublime.packages_path()
sys.path += [
	# TODO add path for host platform
	os.path.join(PACKAGES_PATH, 'PyV8', 'osx')
]

import PyV8

_diff_state = {}
_patch_state = {}

def read_js(file_path):
	with codecs.open(os.path.normpath(file_path), 'r', 'utf-8') as f:
		return f.read()

###############################
# Diff
###############################

def prepare_diff(buf_id):
	"Prepare buffer for diff'ing"
	view = eutils.view_for_buffer_id(buf_id)
	if not view:
		return

	if buf_id not in _diff_state:
		_diff_state[buf_id] = {'running': False, 'required': False, 'content': ''}

	_diff_state[buf_id]['content'] = eutils.content(view)

def diff(buf_id, callback):
	"""
	Performs diff'ing of two states of the same file
	in separate thread and sends generated patch 
	to `callback` function
	"""
	if buf_id not in _diff_state:
		print('Prepare buffer')
		prepare_diff(buf_id)
		callback(None)
		return

	state = _diff_state[buf_id]
	if state['running']:
		state['required'] = True
	else:
		_start_diff(buf_id, callback)

def _run_diff(src1, src2, callback):
	try:
		with PyV8.JSContext(extensions=['livestyle']) as c:
			patches = c.locals.livestyle.diff(src1, src2)
			callback(patches)
	except Exception as e:
		@eutils.main_thread
		def _err():
			print('Error: %s' % e)
			callback(None)

		_err()

def _start_diff(buf_id, callback):
	view = eutils.view_for_buffer_id(buf_id)
	if not view:
		return

	state = _diff_state[buf_id]
	prev_content = state['content']
	content = eutils.content(view)

	@eutils.main_thread
	def _c(result):
		callback(buf_id, result)

		if buf_id in _diff_state:
			state = _diff_state[buf_id]
			state['running'] = False
			if result is not None:
				state['content'] = content

			if state['required']:
				diff(buf_id, callback)
	
	state['required'] = False
	state['running'] = True
	with PyV8.JSLocker():
		threading.Thread(target=_run_diff, args=(prev_content, content, _c)).start()

###############################
# Patch
###############################

def patch(buf_id, patch, callback):
	"""
	Performs patching of given source in separate thread and dispatches
	result into `callback` function
	"""
	if buf_id not in _patch_state:
		_patch_state[buf_id] = {
			'running': False,
			'patches': None
		}

	state = _patch_state[buf_id]

	if patch:
		if not eutils.isstr(patch):
			patch = json.dumps(patch)

		with PyV8.JSLocker():
			with PyV8.JSContext(extensions=['livestyle']) as c:
				state['patches'] = c.locals.livestyle.condensePatches(state['patches'], patch)

	if not state['running'] and state['patches']:
		_patches = state['patches']
		state['patches'] = None
		_start_patch(buf_id, _patches, callback)

def _start_patch(buf_id, patch, callback):
	view = eutils.view_for_buffer_id(buf_id)
	if not view:
		return

	content = eutils.content(view)

	@eutils.main_thread
	def _c(result):
		callback(buf_id, result)

		if buf_id in _patch_state:
			state = _patch_state[buf_id]
			state['running'] = False
			if state['patches']:
				patch(buf_id, None, callback)

	_patch_state[buf_id]['running'] = True
	with PyV8.JSLocker():
		threading.Thread(target=_run_patch, args=(content, patch, _c)).start()

def _run_patch(content, patch, callback):
	try:
		with PyV8.JSContext(extensions=['livestyle']) as c:
			result = c.locals.livestyle.patch(content, patch)
			callback(result)
	except Exception as e:
		@eutils.main_thread
		def _err():
			print('Error: %s' % e)
			callback(None)

		_err()


###############################
# Init JS context
###############################

js_livestyle = read_js(os.path.join(BASE_PATH, '..', 'livestyle-src.js'))
js_emmet = read_js(os.path.join(BASE_PATH, '..', 'emmet.js'))
js_ext = PyV8.JSExtension('livestyle', '\n'.join([js_emmet, js_livestyle]))

del js_emmet, js_livestyle
