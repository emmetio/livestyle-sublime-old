import json
import re
import threading
import sys
import os.path
import imp
import select

import sublime
import sublime_plugin


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

import lsutils.editor as eutils
import lsutils.diff
if 'lsutils.editor' in sys.modules:
	imp.reload(sys.modules['lsutils.editor'])

if 'lsutils.diff' in sys.modules:
	imp.reload(sys.modules['lsutils.diff'])

sublime_ver = int(sublime.version()[0])

# Tornado server instance
httpserver = None

_suppressed = set()

class WSHandler(tornado.websocket.WebSocketHandler):
	clients = set()
	def open(self):
		print('connection opened')
		WSHandler.clients.add(self)
		identify_editor(self)
	
	def on_message(self, message):
		# print('message received:\n%s' % message)
		message = json.loads(message)
		if message['action'] == 'update':
			handle_patch_request(message['data'])
			send_message(message, exclude=self)

	def on_close(self):
		print('connection closed')
		WSHandler.clients.discard(self)

def send_message(message, client=None, exclude=None):
	"Sends given message to websocket clients"
	if not eutils.isstr(message):
		message = json.dumps(message)
	clients = WSHandler.clients if not client else [client]
	if exclude:
		clients = [c for c in clients if c != exclude]

	if not clients:
		print("Websocket is not available: client list empty")
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

	print(p)
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
	print('Handle CSS patch request')

	editor_file = payload.get('editorFile')
	if not editor_file:
		print('No editor file')
		return

	view = eutils.view_for_file(editor_file)
	if view is None:
		print('Unable to find view for %s file, open new one' % editor_file)
		view = sublime.active_window().open_file(editor_file)

	patch = payload.get('patch')
	if patch:
		while view.is_loading():
			pass

		lsutils.diff.patch(view.buffer_id(), patch, apply_patched_source)


def apply_patched_source(buf_id, content):
	view = eutils.view_for_buffer_id(buf_id)
	if not view:
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
	return WSHandler.clients and eutils.is_css_view(view)

def suppress_update(view):
	"Marks given view to skip next incoming update"
	_suppressed.add(view.id())


class LivestyleListener(sublime_plugin.EventListener):
	def on_load(self, view):
		update_files()

	def on_close(self, view):
		update_files()

	def on_modified(self, view):
		if should_handle(view):
			print('Run diff')
			lsutils.diff.diff(view.buffer_id(), send_patches)

	def on_activated(self, view):
		if eutils.is_css_view(view):
			print('Prepare diff')
			lsutils.diff.prepare_diff(view.buffer_id())

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

def start_server(port, ctx=None):
	print('Starting LiveStyle server on port %s' % port)
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
		print('Stopping server')
		server.stop()

	tornado.ioloop.IOLoop.instance().stop()

def unload_handler():
	stop_server()

def start_plugin():
	start_server(54000)

# Init plugin
def plugin_loaded():
	sublime.set_timeout(start_plugin, 100)

if sublime_ver < 3:
	plugin_loaded()