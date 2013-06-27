import sublime
import sublime_plugin

import sys
import threading
import os.path
import codecs
import time

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

def read_js(file_path):
	f = codecs.open(os.path.normpath(file_path), 'r', 'utf-8')
	content = f.read()
	f.close()
	return content

def prepare(buf_id):
	"Prepare buffer for patching"
	view = eutils.view_for_buffer_id(buf_id)
	if not view:
		return

	if buf_id not in _diff_state:
		_diff_state[buf_id] = {'running': False, 'required': False, 'content': ''}

	_diff_state[buf_id]['content'] = eutils.content(view)

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
			if not state['content'] and result is not None:
				state['content'] = content

			if state['required']:
				diff(buf_id, callback)

	
	
	state['required'] = False
	state['running'] = True
	state['content'] = ''
	t = threading.Thread(target=_run_diff, args=(prev_content, content, _c))
	with PyV8.JSLocker():
		t.start()


def diff(buf_id, callback):
	if buf_id not in _diff_state:
		print('Prepare buffer')
		prepare(buf_id)
		callback(None)
		return

	state = _diff_state[buf_id]
	if state['running']:
		state['required'] = True
	else:
		_start_diff(buf_id, callback)

js_ext = PyV8.JSExtension('livestyle', read_js(os.path.join(BASE_PATH, '..', 'livestyle-src.js')))
