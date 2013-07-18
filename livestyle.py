import json
import re
import threading
import sys
import os.path
import imp
import select
import logging

import sublime
import sublime_plugin

DEFAULT_PORT = 5400
BASE_PATH = os.path.abspath(os.path.dirname(__file__))
PACKAGES_PATH = sublime.packages_path() or os.path.dirname(BASE_PATH)
sys.path += [
	BASE_PATH
]

# don't know why, but tornado's IOLoop cannot
# properly load platform modules during runtime, 
# so we pre-import them
if hasattr(select, "epoll"):
	import tornado.platform.epoll
elif hasattr(select, "kqueue"):
	import tornado.platform.kqueue
else:
	import tornado.platform.select

import tornado.httpserver
import tornado.websocket
import tornado.ioloop
import tornado.web


# Make sure all dependencies are reloaded on upgrade
if 'lsutils.reloader' in sys.modules:
	imp.reload(sys.modules['lsutils.reloader'])
import lsutils.reloader

import lsutils.editor as eutils
import lsutils.diff

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

	def on_close(self):
		logger.info('client disconnected')
		WSHandler.clients.discard(self)

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

	logger.debug(p)
	p = json.loads(p)
	view = eutils.view_for_buffer_id(buf_id)
	if p and view:
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
		while view.is_loading():
			pass

		# make sure it's a CSS file
		if not eutils.is_css_view(view, True):
			logger.debug('File %s is not CSS, aborting' % eutils.file_name(view))
			return

		# looks like view.window() is broken in ST2,
		# use another way to find parent window
		for w in sublime.windows():
			for v in w.views():
				if v.id() == view.id():
					w.focus_view(v)

		lsutils.diff.patch(view.buffer_id(), patch, apply_patched_source)


def apply_patched_source(buf_id, content):
	view = eutils.view_for_buffer_id(buf_id)
	if not view or content is None:
		return

	if sublime_ver < 3:
		content = content.decode('utf-8')

	view.run_command('livestyle_replace_content', {'content': content})


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
	stop_server()
	start_server(int(settings.get('port', DEFAULT_PORT)))
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
	"Internal command to properly replace view content"
	def run(self, edit, content=None, **kw):
		# _cache['supress_modification'] = True
		suppress_update(self.view)
		self.view.replace(edit, sublime.Region(0, self.view.size()), content)

# XXX init
application = tornado.web.Application([
	(r'/browser', WSHandler),
])

def start_server(port):
	logger.info('Starting LiveStyle server on port %s' % port)
	server = tornado.httpserver.HTTPServer(application)
	server.listen(port, address='127.0.0.1')
	threading.Thread(target=tornado.ioloop.IOLoop.instance().start).start()
	globals()['httpserver'] = server

def stop_server():
	for c in WSHandler.clients.copy():
		c.close()
	WSHandler.clients.clear()

	server = globals().get('httpserver', None)
	if server:
		logger.info('Stopping server')
		server.stop()

	tornado.ioloop.IOLoop.instance().stop()

def unload_handler():
	stop_server()

def start_plugin():
	globals()['settings'] = sublime.load_settings('LiveStyle.sublime-settings')
	
	start_server(int(settings.get('port', DEFAULT_PORT)))
	logger.setLevel(logging.DEBUG if settings.get('debug', False) else logging.INFO)
	settings.add_on_change('settings', handle_settings_change)

	# collect all view's file paths
	for view in eutils.all_views():
		_view_file_names[view.id()] = eutils.file_name(view)

# Init plugin
def plugin_loaded():
	sublime.set_timeout(start_plugin, 100)

if sublime_ver < 3:
	plugin_loaded()