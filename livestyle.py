import json
import re
import threading
import sys
import os.path
import platform
import imp
import logging
import json

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

# don't know why, but tornado's IOLoop cannot
# properly load platform modules during runtime, 
# so we pre-import them
try:
	import select

	if hasattr(select, "epoll"):
		import tornado.platform.epoll
	elif hasattr(select, "kqueue"):
		import tornado.platform.kqueue
	else:
		import tornado.platform.select
except ImportError:
	pass

# import tornado.process
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.websocket
import tornado.httpserver


# Make sure all dependencies are reloaded on upgrade
if 'lsutils.reloader' in sys.modules:
	imp.reload(sys.modules['lsutils.reloader'])
import lsutils.reloader

import lsutils.editor as eutils
import lsutils.diff
import lsutils.webkit_installer

sublime_ver = int(sublime.version()[0])

# Tornado server instance
httpserver = None

_suppressed = set()

# List of all opened views and their file names
_view_file_names = {}

# Plugin settings
settings = None

# Create logger
logger = logging.getLogger('livestyle')
logger.propagate = False
if not logger.handlers:
	ch = logging.StreamHandler()
	ch.setLevel(logging.DEBUG)
	ch.setFormatter(logging.Formatter('Emmet LiveStyle: %(message)s'))
	logger.addHandler(ch)

def send_message(message, client=None, exclude=None):
	"Sends given message to websocket clients"
	if not eutils.isstr(message):
		message = json.dumps(message)
	clients = WSHandler.clients if not client else [client]
	if exclude:
		clients = [c for c in clients if c != exclude]

	if not clients:
		logger.debug('Cannot send message, client list empty')
	else:
		for c in clients:
			c.write_message(message)


def parse_json(data):
	return json.loads(data) if eutils.isstr(data) else data

@eutils.main_thread
def identify_editor(socket):
	"Sends editor identification info to browser"
	send_message({
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
	send_message({
		'action': 'updateFiles',
		'data': eutils.css_files()
	})

@eutils.main_thread
def send_patches(buf_id=None, p=None):
	if not buf_id or not p:
		return

	p = parse_json(p)
	view = eutils.view_for_buffer_id(buf_id)
	if p and view is not None:
		send_message({
			'action': 'update',
			'data': {
				'editorFile': eutils.file_name(view),
				'patch': p
			}
		})

@eutils.main_thread
def handle_patch_request(payload):
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
	"Waita until view is loaded and applies patch on it"
	if view.is_loading():
		return sublime.set_timeout(lambda: apply_patch_on_view(view, patch), 100)

	# make sure it's a CSS file
	if not eutils.is_css_view(view, True):
		logger.debug('File %s is not CSS, aborting' % eutils.file_name(view))
		return

	focus_view(view)
	lsutils.diff.patch(view.buffer_id(), patch, apply_patched_source)

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
	return WSHandler.clients and eutils.is_css_view(view, True)

def suppress_update(view):
	"Marks given view to skip next incoming update"
	_suppressed.add(view.id())

def handle_settings_change():
	if not settings: 
		return

	stop_server()
	start_server(int(settings.get('port')))
	logger.setLevel(logging.DEBUG if settings.get('debug', False) else logging.INFO)

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
			lsutils.diff.diff(view.buffer_id(), send_patches)

	def on_activated(self, view):
		if eutils.is_css_view(view, True):
			logger.debug('Prepare diff')
			lsutils.diff.prepare_diff(view.buffer_id())

	def on_post_save(self, view):
		k = view.id()
		new_name = eutils.file_name(view)
		if k in _view_file_names and _view_file_names[k] != new_name:
			send_message({
				'action': 'renameFile',
				'data': {
					'oldname': _view_file_names[k],
					'newname': new_name
				}
			})
			_view_file_names[k] = new_name


class LivestyleReplaceContentCommand(sublime_plugin.TextCommand):
	"Internal command to psroperly replace view content"
	def run(self, edit, payload=None, **kwargs):
		if not payload:
			return

		suppress_update(self.view)
		s = self.view.sel()[0]
		sels = [[s.a, s.a]]
		
		try:
			payload = parse_json(payload)
		except:
			payload = {'content': payload, 'selection': None}

		if sublime_ver < 3:
			payload['content'] = payload.get('content', '').decode('utf-8')

		self.view.replace(edit, sublime.Region(0, self.view.size()), payload.get('content'))

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


# XXX init
# Server app

class WSHandler(tornado.websocket.WebSocketHandler):
	clients = set()
	def open(self):
		logger.info('client connected')
		WSHandler.clients.add(self)
		identify_editor(self)
	
	def on_message(self, message):
		logger.debug('message received:\n%s' % message)
		message = json.loads(message)
		if message['action'] == 'update':
			handle_patch_request(message['data'])
			send_message(message, exclude=self)
		elif message['action'] == 'error':
			logger.error('[client] %s' % message['data'].get('message'))

	def on_close(self):
		logger.info('client disconnected')
		WSHandler.clients.discard(self)


class LiveStyleIDHandler(tornado.web.RequestHandler):
	def get(self):
		self.write('LiveStyle websockets server is up and running')

application = tornado.web.Application([
	(r'/browser', WSHandler),
	(r'/', LiveStyleIDHandler)
])

def start_server(port):
	global httpserver
	logger.info('Starting LiveStyle server on port %s' % port)
	httpserver = tornado.httpserver.HTTPServer(application)
	httpserver.listen(port, address='127.0.0.1')
	threading.Thread(target=tornado.ioloop.IOLoop.instance().start).start()

def stop_server():
	global httpserver
	for c in WSHandler.clients.copy():
		c.close()
	WSHandler.clients.clear()

	if httpserver:
		logger.info('Stopping server')
		httpserver.stop()

	tornado.ioloop.IOLoop.instance().stop()

def unload_handler():
	stop_server()

def start_plugin():
	global settings
	settings = sublime.load_settings('LiveStyle.sublime-settings')
	
	start_server(int(settings.get('port')))
	logger.setLevel(logging.DEBUG if settings.get('debug', False) else logging.INFO)
	settings.add_on_change('settings', handle_settings_change)

	lsutils.diff.import_pyv8()

	# collect all view's file paths
	for view in eutils.all_views():
		_view_file_names[view.id()] = eutils.file_name(view)

# Init plugin
def plugin_loaded():
	sublime.set_timeout(start_plugin, 100)

if sublime_ver < 3:
	plugin_loaded()