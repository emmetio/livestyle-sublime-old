import sublime
import sublime_plugin

import sys
import threading
import os.path
import codecs
import time
import json
import logging
import imp

import lsutils.editor as eutils
import lsutils.pyv8delegate
import lsutils.pyv8loader

for p in lsutils.pyv8delegate.PYV8_PATHS:
	if p not in sys.path:
		sys.path.append(p)

try:
	isinstance("", basestring)
	def isstr(s):
		return isinstance(s, basestring)
except NameError:
	def isstr(s):
		return isinstance(s, str)

BASE_PATH = os.path.abspath(os.path.dirname(__file__))
logger = logging.getLogger('livestyle')
_diff_state = {}
_patch_state = {}

def read_js(file_path, use_unicode=True):
	file_path = os.path.normpath(file_path)
	if hasattr(sublime, 'load_resource'):
		rel_path = None
		for prefix in [sublime.packages_path(), sublime.installed_packages_path()]:
			if file_path.startswith(prefix):
				rel_path = os.path.join('Packages', file_path[len(prefix) + 1:])
				break

		if rel_path:
			rel_path = rel_path.replace('.sublime-package', '')
			# for Windows we have to replace slashes
			# print('Loading %s' % rel_path)
			rel_path = rel_path.replace('\\', '/')
			return sublime.load_resource(rel_path)

	if use_unicode:
		f = codecs.open(file_path, 'r', 'utf-8')
	else:
		f = open(file_path, 'r')

	content = f.read()
	f.close()

	return content

def has_pyv8():
	"Check if PyV8 is available"
	return 'PyV8' in sys.modules and 'PyV8' in globals()

def import_pyv8():
	if not has_pyv8():
		# Importing non-existing modules is a bit tricky in Python:
		# if we simply call `import PyV8` and module doesn't exists,
		# Python will cache this failed import and will always
		# throw exception even if this module appear in PYTHONPATH.
		# To prevent this, we have to manually test if
		# PyV8.py(c) exists in PYTHONPATH before importing PyV8
		if 'PyV8' in sys.modules and 'PyV8' not in globals():
			# PyV8 was loaded by ST, create global alias
			globals()['PyV8'] = __import__('PyV8')
		else:
			loaded = False
			f, bin_f = None, None
			try:
				f, pathname, description = imp.find_module('PyV8')
				bin_f, bin_pathname, bin_description = imp.find_module('_PyV8')
				if f:
					imp.acquire_lock()
					globals()['_PyV8'] = imp.load_module('_PyV8', bin_f, bin_pathname, bin_description)
					globals()['PyV8'] = imp.load_module('PyV8', f, pathname, description)
					imp.release_lock()
					loaded = True
			except ImportError as e:
				logger.error('Failed to import: %s' % e)
				return False
			finally:
				# Since we may exit via an exception, close fp explicitly.
				if f: f.close()
				if bin_f: bin_f.close()

			if not loaded:
				return False

		if 'PyV8' not in sys.modules:
			# Binary is not available yet
			return False

		# Binary just loaded, create extensions
		try:
			js_livestyle = read_js(os.path.join(BASE_PATH, '..', 'livestyle-src.js'))
		except:
			js_livestyle = read_js(os.path.join(BASE_PATH, '..', 'livestyle.js'))

		js_emmet = read_js(os.path.join(BASE_PATH, '..', 'emmet.js'))
		js_ext = PyV8.JSExtension('livestyle', '\n'.join([js_emmet, js_livestyle]))

	return True

def get_syntax(view):
	return view.score_selector(0, 'source.less, source.scss') and 'scss' or 'css'

###############################
# Diff
###############################

def prepare_diff(buf_id):
	"Prepare buffer for diff'ing"
	if not has_pyv8(): return

	view = eutils.view_for_buffer_id(buf_id)
	if view is None:
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
	if not import_pyv8():
		logger.error('PyV8 is not available')
		return

	if buf_id not in _diff_state:
		logger.debug('Prepare buffer')
		prepare_diff(buf_id)
		callback(None)
		return

	state = _diff_state[buf_id]
	if state['running']:
		state['required'] = True
	else:
		_start_diff(buf_id, callback)

def _run_diff(src1, src2, syntax, callback):
	# @eutils.main_thread
	def _err(e):
		logger.error('Error: %s' % e)
		callback(None)

	try:
		with PyV8.JSContext(extensions=['livestyle']) as c:
			r = c.locals.livestyle.diff(src1, src2, syntax)
			result = json.loads(c.locals.livestyle.diff(src1, src2, syntax))
			if result['status'] == 'ok':
				callback(result['patches'])
			else:
				_err(result['error'])
	except Exception as e:
		_err(e)

def _start_diff(buf_id, callback):
	view = eutils.view_for_buffer_id(buf_id)
	if view is None:
		return

	state = _diff_state[buf_id]
	prev_content = state['content']
	content = eutils.content(view)
	syntax = get_syntax(view)

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
		threading.Thread(target=_run_diff, args=(prev_content, content, syntax, _c)).start()

###############################
# Patch
###############################

def patch(buf_id, patch, callback):
	"""
	Performs patching of given source in separate thread and dispatches
	result into `callback` function
	"""
	if not import_pyv8():
		logger.error('PyV8 is not available')
		return

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
	if view is None:
		return

	content = eutils.content(view)
	syntax = get_syntax(view)

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
		threading.Thread(target=_run_patch, args=(content, patch, syntax, _c)).start()

def _run_patch(content, patch, syntax, callback):
	def _err(e):
		logger.error('Error while patching: %s' % e)
		callback(None)

	try:
		with PyV8.JSContext(extensions=['livestyle']) as c:
			r = c.locals.livestyle.patchAndDiff(content, patch, syntax)
			result = json.loads(r)
			if result['status'] == 'ok':
				callback(result)
			else:
				_err(result['error'])
	except Exception as e:
		_err(e)

def is_valid_patch(content):
	"Check if given content is a valid patch"
	if isstr(content):
		try:
			content = json.loads(content)
		except:
			return False

	try:
		return content and content.get('id') == 'livestyle'
	except:
		return False

def parse_patch(data):
	"Parses given patch and returns object with meta-data about patch"
	if not is_valid_patch(data):
		return None

	if isstr(data): data = json.loads(data)
	out = []
	for k, v in data.get('files', {}).items():
		out.append({
			'file': k,
			'selectors': _stringify_selectors(v),
			'data': v
		})

	return out

def _stringify_selectors(patch):
	"Stringifies updated selectors. Mostly used for deceision making"
	out = []
	for p in patch:
		if p['action'] == 'remove':
			# No need to display removed selectors since, in most cases,
			# they are mostly garbage left during typing
			continue

		out.append('/'.join(s[0] for s in p['path']))

	return out


###############################
# Init JS context
###############################

def _cb(status):
	if status:
		import_pyv8()

# import_pyv8()
delegate = lsutils.pyv8delegate.LoaderDelegate(callback=_cb)
lsutils.pyv8loader.load(lsutils.pyv8delegate.PYV8_PATHS[1], delegate)
