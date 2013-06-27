import json
import re
import threading
import sys
import os.path
import imp

import sublime
import sublime_plugin


BASE_PATH = os.path.abspath(os.path.dirname(__file__))
PACKAGES_PATH = sublime.packages_path() or os.path.dirname(BASE_PATH)
sys.path += [
	BASE_PATH, 
	os.path.join(BASE_PATH, 'tornado')
]

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

try:
	isinstance("", basestring)
	def isstr(s):
		return isinstance(s, basestring)
except NameError:
	def isstr(s):
		return isinstance(s, str)

class WSHandler(tornado.websocket.WebSocketHandler):
	clients = set()
	def open(self):
		print('connection opened')
		WSHandler.clients.add(self)
		identify_editor(self)
	
	def on_message(self, message):
		# print('message received:\n%s' % message)
		message = json.loads(message)
		# if message['action'] == 'update':
		# 	update_css(message['data'])
		# 	send_message(message, exclude=self)

	def on_close(self):
		print('connection closed')
		WSHandler.clients.discard(self)

def send_message(message, client=None, exclude=None):
	"Sends given message to websocket clients"
	if not isstr(message):
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

	view = eutils.view_for_buffer_id(buf_id)
	if view:
		print('Sending patch')
		send_message({
			'action': 'update',
			'data': {
				'editorFile': eutils.file_name(view),
				'patch': json.loads(p)
			}
		})

def should_handle(view):
	# don't do anything if there are no connected clients
	# or change performed outside of CSS file
	return WSHandler.clients and eutils.is_css_view(view)

class LiveStyleListener(sublime_plugin.EventListener):
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
			lsutils.diff.prepare(view.buffer_id())


# class LivestyleBuildTree(sublime_plugin.TextCommand):
# 	def run(self, edit, **kw):
# 		lsutils.tree.build(eutils.active_view())

# class LivestylePrepareDiff(sublime_plugin.TextCommand):
# 	def run(self, edit, **kw):
# 		print('Prepare diff')
# 		lsutils.diff.prepare(eutils.active_view().buffer_id())

# class LivestyleRunDiff(sublime_plugin.TextCommand):
# 	def run(self, edit, **kw):
# 		print('Run diff')
# 		def callback(buf_id=None, p=None):
# 			print('Patch: %s' % p)

# 		lsutils.diff.diff(eutils.active_view().buffer_id(), callback)

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