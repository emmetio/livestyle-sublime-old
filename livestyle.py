import json
import re
import threading
import sys
import os.path
import platform
import imp
import logging
import json
import codecs

import sublime
import sublime_plugin

BASE_PATH = os.path.abspath(os.path.dirname(__file__))
for p in [BASE_PATH, os.path.join(BASE_PATH, 'lsutils')]:
	if p not in sys.path:
		sys.path.append(p)

# need the windows select.pyd binary for ST2 only
if os.name == 'nt' and sublime.version()[0] < '3':
	__file = os.path.normpath(os.path.abspath(__file__))
	__path = os.path.dirname(__file)
	libs_path = os.path.join(__path, 'lsutils', 'libs', platform.architecture()[0])
	if libs_path not in sys.path:
		sys.path.insert(0, libs_path)


# Make sure all dependencies are reloaded on upgrade
if 'lsutils.reloader' in sys.modules:
	imp.reload(sys.modules['lsutils.reloader'])
import lsutils.reloader

import lsutils.editor as eutils
import lsutils.diff
import lsutils.websockets as ws
import lsutils.webkit_installer

sublime_ver = int(sublime.version()[0])

_suppressed = set()

# List of all opened views and their file names
_view_file_names = {}

# Create logger
logger = logging.getLogger('livestyle')
logger.propagate = False
if not logger.handlers:
	ch = logging.StreamHandler()
	ch.setLevel(logging.DEBUG)
	ch.setFormatter(logging.Formatter('Emmet LiveStyle: %(message)s'))
	logger.addHandler(ch)

@eutils.main_thread
def identify_editor(socket):
	"Sends editor identification info to browser"
	ws.send({
		'action': 'id',
		'data': {
			'id': 'st%d' % sublime_ver,
			'title': 'Sublime Text %d' % sublime_ver,
			'icon': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABu0lEQVR42q2STWsTURhG3WvdCyq4CEVBAgYCM23JjEwy+cJC41gRdTIEGyELU7BNNMJQhUBBTUjSRdRI3GThRld+gbj2JwhuRFy5cZ3Ncd5LBwZCIIIXDlzmeZ9z4d458t9WoVB4XywWCcnn89i2TSaTIZvNEuRhJvtP0e7R6XT6VYJer8dkMmE0GrHf3uPxg1s8f+TR9ncZDocq63a7SiId6YogBqiPg8FASe43d3iz7/D7rcuP1zf4NnHxfV9yQc0CSFcEeihotVo0Gg22tzbh3SbP7lq4lzTuuHlqtZrkQlSgi8AIBZVKBc/zuH5lnc7tFX4OL/L9wOTJlsbGepFyuSwzUYERCqIXhGVZJJNJbqbP0b66DC8ucO/yedLptMzMF4S3X7JXeFWJ4Zln2LZPw9NT+BuxxQTquaw1Xl47yZ/WEr92j3PgnMBc08nlcvMF1Wo1DNW7G4aBpmnouo5pmtGyzM4K+v0+4/F4ITqdzqzAdV0cxyGVSsmpc5G/s1QqzQg+N5tNdUmJRIJ4PD4XkdTrdaQTClYDlvnHFXTOqu7h5mHAx4AvC/IhYE+6IliK2IwFWT3sHPsL6BnLQ4kfGmsAAAAASUVORK5CYII=',
			'files': eutils.css_files()
		}
	}, socket)

@eutils.main_thread
def update_files():
	ws.send({
		'action': 'updateFiles',
		'data': eutils.css_files()
	})

def send_patches(buf_id=None, p=None):
	if not buf_id or not p:
		return

	p = eutils.parse_json(p)
	view = eutils.view_for_buffer_id(buf_id)
	if p and view is not None:
		ws.send({
			'action': 'update',
			'data': {
				'editorFile': eutils.file_name(view),
				'patch': p
			}
		})

def read_file(file_path):
	try:
		with codecs.open(file_path, 'r', 'utf-8') as f:
			return f.read()
	except Exception as e:
		logger.error(e)
		return None

@eutils.main_thread
def send_unsaved_files(payload, sender):
	files = payload.get('files', [])
	out = []
	for f in files:
		view = eutils.view_for_file(f)
		if not view:
			continue

		content = eutils.content(view)
		if view and view.is_dirty():
			fname = view.file_name()
			pristine = None
			if not fname:
				# untitled file
				pristine = ''
			elif os.path.exists(fname):
				pristine = read_file(fname)

			if pristine is not None:
				out.append({
					'file': f,
					'pristine': pristine,
					'content': content
				})

	if out:
		ws.send({
			'action': 'unsavedFiles',
			'data': {
				'files': out
			}
		}, sender)
	else:
		logger.info('No unsaved changes')

@eutils.main_thread
def handle_patch_request(payload, sender):
	logger.debug('Handle CSS patch request')

	editor_file = payload.get('editorFile')
	if not editor_file:
		logger.debug('No editor file in payload, skip patching')
		return

	view = eutils.view_for_file(editor_file)
	if view is None:
		logger.warn('Unable to find view for %s file' % editor_file)
		if editor_file[0] == '<':
			# it's an untitled file, but view doesn't exists
			return

		view = sublime.active_window().open_file(editor_file)

	patch = payload.get('patch')
	if patch:
		apply_patch_on_view(view, patch)

def apply_patch_on_view(view, patch):
	"Waits until view is loaded and applies patch on it"
	if view.is_loading():
		return sublime.set_timeout(lambda: apply_patch_on_view(view, patch), 100)

	# make sure it's a CSS file
	if not eutils.is_css_view(view, True):
		logger.debug('File %s is not CSS, aborting' % eutils.file_name(view))
		return

	focus_view(view)
	lsutils.diff.patch(view.buffer_id(), patch)

def focus_view(view):
	# looks like view.window() is broken in ST2,
	# use another way to find parent window
	for w in sublime.windows():
		for v in w.views():
			if v.id() == view.id():
				return w.focus_view(v)

def apply_patched_source(buf_id, content):
	view = eutils.view_for_buffer_id(buf_id)
	if view is None or content is None:
		return

	view.run_command('livestyle_replace_content', {'payload': content})


def should_handle(view):
	"Checks whether incoming view modification should be handled"
	if view.id() in _suppressed:
		_suppressed.remove(view.id())
		return False

	# don't do anything if there are no connected clients
	# or change performed outside of CSS file
	return ws.clients() and eutils.is_css_view(view, True)

def suppress_update(view):
	"Marks given view to skip next incoming update"
	_suppressed.add(view.id())

class LivestyleListener(sublime_plugin.EventListener):
	def on_new(self, view):
		_view_file_names[view.id()] = eutils.file_name(view)

		if eutils.is_css_view(view):
			update_files()

	def on_load(self, view):
		_view_file_names[view.id()] = eutils.file_name(view)

		if eutils.is_css_view(view):
			update_files()

	def on_close(self, view):
		if view.id() in _view_file_names:
			del _view_file_names[view.id()]

		update_files()

	def on_modified(self, view):
		if should_handle(view):
			logger.debug('Run diff')
			lsutils.diff.diff(view.buffer_id())

	def on_activated(self, view):
		if eutils.is_css_view(view, True):
			logger.debug('Prepare diff')
			update_files()
			lsutils.diff.prepare_diff(view.buffer_id())

	def on_post_save(self, view):
		k = view.id()
		new_name = eutils.file_name(view)
		if k in _view_file_names and _view_file_names[k] != new_name:
			ws.send({
				'action': 'renameFile',
				'data': {
					'oldname': _view_file_names[k],
					'newname': new_name
				}
			})
			_view_file_names[k] = new_name


class LivestyleReplaceContentCommand(sublime_plugin.TextCommand):
	"Internal command to properly replace view content"
	def run(self, edit, payload=None, **kwargs):
		if not payload:
			return

		suppress_update(self.view)
		s = self.view.sel()[0]
		sels = [[s.a, s.a]]
		
		try:
			payload = eutils.parse_json(payload)
		except:
			payload = {'content': payload, 'selection': None}

		# if sublime_ver < 3:
		#	payload['content'] = payload.get('content', u'').decode('utf-8')

		self.view.replace(edit, sublime.Region(0, self.view.size()), payload.get('content', ''))

		if payload.get('selection'):
			sels = [payload.get('selection')]

		self.view.sel().clear()
		for s in sels:
			self.view.sel().add(sublime.Region(s[0], s[1]))

		self.view.show(self.view.sel())

class LivestyleInstallWebkitExt(sublime_plugin.ApplicationCommand):
	def run(self, *args, **kw):
		try:
			lsutils.webkit_installer.install()
			sublime.message_dialog('WebKit extension installed successfully. Please restart WebKit.')
		except lsutils.webkit_installer.LSIException as e:
			sublime.error_message('Unable to install WebKit extension:\n%s' % e.message)
		except Exception as e:
			sublime.error_message('Error during WebKit extension installation:\n%s' % e)

	def description(*args, **kwargs):
		return 'Install LiveStyle for WebKit extension'

class LivestyleApplyPatch(sublime_plugin.TextCommand):
	"Applies LiveStyle patch to active view"
	def run(self, edit, **kw):
		if not eutils.is_css_view(self.view, True):
			return sublime.error_message('You should run this action on CSS file')

		# build sources list
		sources = [view for view in eutils.all_views() if re.search(r'[\/\\]lspatch-[\w\-]+\.json$', view.file_name() or '')]

		# gather all available items
		display_items = []
		patches = []

		def add_item(patch, name):
			for p in patch:
				display_items.append([p['file'], 'Updated selectors: %s' % ', '.join(p['selectors']), name])
				patches.append(json.dumps(p['data']))

		for view in sources:
			add_item(lsutils.diff.parse_patch(eutils.content(view)), view.file_name())

		# check if buffer contains valid patch
		pb =  sublime.get_clipboard()
		if lsutils.diff.is_valid_patch(pb):
			add_item(lsutils.diff.parse_patch(pb), 'Clipboard')

		def on_done(ix):
			if ix == -1: return
			apply_patch_on_view(self.view, patches[ix])

		if len(display_items) == 1:
			on_done(0)
		elif display_items:
			self.view.window().show_quick_panel(display_items, on_done)
		else:
			sublime.error_message('No patches found. You have to open patch files in Sublime Text or copy patch file contents into clipboard and run this action again.')

def unload_handler():
	ws.stop()

def start_plugin():
	ws.start(int(eutils.get_setting('port')))
	logger.setLevel(logging.DEBUG if eutils.get_setting('debug', False) else logging.INFO)

	# collect all view's file paths
	for view in eutils.all_views():
		_view_file_names[view.id()] = eutils.file_name(view)


def plugin_loaded():
	sublime.set_timeout(start_plugin, 100)

# Init plugin
ws.on('update', handle_patch_request)
ws.on('requestUnsavedFiles', send_unsaved_files)
ws.on('ws_open', identify_editor)
lsutils.diff.on('diff_complete', send_patches)
lsutils.diff.on('patch_complete', apply_patched_source)

if sublime_ver < 3:
	plugin_loaded()