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

import lsutils.depgraph as depgraph
import lsutils.editor as eutils
import lsutils.websockets as ws

from lsutils.event_dispatcher import EventDispatcher

LOCK_TIMEOUT = 15 # State lock timeout, in seconds

logger = logging.getLogger('livestyle')
_diff_state = {}
_patch_state = {}
_dispatcher = EventDispatcher()

def on(name, callback):
	_dispatcher.on(name, callback)

def off(name, callback=None):
	_dispatcher.off(name, callback)

def one(name, callback):
	_dispatcher.one(name, callback)

def lock_state(state):
	state['running'] = True
	state['start_time'] = time.time()

def unlock_state(state, log_message=None):
	state['running'] = False
	if log_message:
		logger.debug(log_message % (time.time() - state['start_time'], ))

def is_locked(state):
	if state['running']:
		return time.time() - state['start_time'] < LOCK_TIMEOUT

	return False

###############################
# Dependencies
###############################

def global_deps(view, syntax):
	"Returns list of project's global dependencies for given view"
	wnd = view.window()

	if not hasattr(wnd, 'project_file_name'):
		return []

	project_data = wnd.project_data()
	if not project_data or not project_data.get('livestyle'):
		return []

	project_file = wnd.project_file_name()
	project_dir = os.path.dirname(project_file)
	ls_data = project_data.get('livestyle')
	key = '%s_globals' % syntax
	files = ls_data.get(key, [])
	return [os.path.join(project_dir, f) for f in files]

def view_deps(view, syntax):
	"Returns dependencies for given view"
	if syntax == 'css':
		return None

	g = global_deps(view, syntax)
	deps = depgraph.dependencies(view.file_name(), eutils.content(view), g)
	return [d.json() for d in deps]

###############################
# Diff
###############################

def prepare_diff(buf_id, syntax):
	"Prepare buffer for diff'ing"
	view = eutils.view_for_buffer_id(buf_id)
	if view is None:
		return

	if buf_id not in _diff_state:
		_diff_state[buf_id] = {
			'running': False, 
			'required': False, 
			'content': '', 
			'syntax': syntax,
			'deps': view_deps(view, syntax),
			'start_time': 0
		}

	_diff_state[buf_id]['content'] = eutils.content(view)

def diff(buf_id, syntax):
	"""
	Performs diff'ing of two states of the same file
	in separate thread
	"""
	if buf_id not in _diff_state:
		logger.debug('Prepare buffer')
		prepare_diff(buf_id, syntax)

	state = _diff_state[buf_id]
	if is_locked(state):
		state['required'] = True
	else:
		_start_diff(buf_id)

def _start_diff(buf_id):
	view = eutils.view_for_buffer_id(buf_id)
	if view is None:
		return

	state = _diff_state[buf_id]
	prev_content = state['content']
	content = eutils.content(view)
	syntax = state['syntax']
	state['required'] = False

	client = ws.find_client({'supports': syntax})

	if client:
		logger.debug('Use connected "%s" client for diff' % client.name())
		lock_state(state)
		ws.send({
			'action': 'diff',
			'data': {
				'file': buf_id,
				'syntax': syntax,
				'source1': prev_content,
				'source2': content,
				'deps': state['deps']
			}
		}, client)
	else:
		logger.error('No suitable client for diff')
		
def _on_diff_complete(buf_id, patches, content):
	_dispatcher.trigger('diff_complete', buf_id, patches)

	if buf_id in _diff_state:
		state = _diff_state[buf_id]
		unlock_state(state, 'Diff performed in %.4fs')
		if patches is not None:
			state['content'] = content

		if state['required']:
			diff(buf_id)

###############################
# Patch
###############################

def patch(buf_id, patches, syntax):
	"""
	Performs patching of given source in separate thread 
	"""
	logger.debug('Request patching')
	if buf_id not in _patch_state:
		view = eutils.view_for_buffer_id(buf_id)
		_patch_state[buf_id] = {
			'running': False,
			'patches': [],
			'syntax': syntax,
			'deps': view_deps(view, syntax),
			'start_time': 0
		}

	state = _patch_state[buf_id]
	patches = eutils.parse_json(patches) or []

	if is_locked(state):
		logger.debug('Batch patches')
		state['patches'] += patches
	elif patches:
		logger.debug('Start patching')
		_start_patch(buf_id, patches)

def _start_patch(buf_id, patch):
	view = eutils.view_for_buffer_id(buf_id)
	if view is None:
		logger.debug('No view to patch')
		return

	content = eutils.content(view)
	state = _patch_state[buf_id]
	syntax = state['syntax']

	client = ws.find_client({'supports': syntax})

	if client:
		logger.debug('Use connected "%s" client for patching %s source' % (client.name(), syntax))
		lock_state(state)
		ws.send({
			'action': 'patch',
			'data': {
				'file': buf_id,
				'syntax': syntax,
				'patches': patch,
				'source': content,
				'deps': state['deps']
			}
		}, client)
	else:
		logger.error('No suitable client for patching')

def _on_patch_complete(buf_id, content):
	_dispatcher.trigger('patch_complete', buf_id, content)

	if buf_id in _patch_state:
		state = _patch_state[buf_id]
		unlock_state(state, 'Patch performed in %.4fs')
		if state['patches']:
			patch(buf_id, state['patches'])
			state['patches'] = []


def is_valid_patch(content):
	"Check if given content is a valid patch"
	if eutils.isstr(content):
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

	if eutils.isstr(data): data = json.loads(data)
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
# Handle Websockets events
###############################

@eutils.main_thread
def _on_diff_editor_sources(data, sender):
	logger.debug('Received diff sources response: %s' % ws.format_message(data))
	if not data['success']:
		logger.error('[ws] %s' % data.get('result', ''))
		_on_diff_complete(data.get('file'), None, None)
	else:
		r = data.get('result', {})
		_on_diff_complete(data.get('file'), r.get('patches'), r.get('source'))

@eutils.main_thread
def _on_patch_editor_sources(data, sender):
	logger.debug('Received patched source: %s' % ws.format_message(data))
	if not data['success']:
		logger.error('[ws] %s' % data.get('result', ''))
		_on_patch_complete(data.get('file'), None)
	else:
		r = data.get('result', {})
		_on_patch_complete(data.get('file'), r)

ws.on('diff', _on_diff_editor_sources)
ws.on('patch', _on_patch_editor_sources)
