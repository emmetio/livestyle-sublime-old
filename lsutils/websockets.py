import json
import logging
import threading

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

import lsutils.editor as eutils
from lsutils.event_dispatcher import EventDispatcher

# Tornado server instance
httpserver = None

logger = logging.getLogger('livestyle')

broadcast_events = ['update']

# Websockets event dispatcher
_dispatcher = EventDispatcher()

class LiveStyleIDHandler(tornado.web.RequestHandler):
	def get(self):
		self.write('LiveStyle websockets server is up and running')

class WSHandler(tornado.websocket.WebSocketHandler):
	clients = set()
	def open(self):
		logger.debug('client connected')
		WSHandler.clients.add(self)
		_dispatcher.trigger('ws_open', self)
	
	def on_message(self, message):
		logger.debug('message received:\n%s' % format_message(message))
		_dispatcher.trigger('ws_message', message, self)

		message = json.loads(message)
		_dispatcher.trigger(message['action'], message.get('data'), self)

		if message['action'] in broadcast_events:
			send(message, exclude=self)

		if message['action'] == 'handshake':
			self.livestyleClientInfo = message['data']
		elif message['action'] == 'error':
			logger.error('[client] %s' % message['data'].get('message'))

	def on_close(self):
		logger.debug('client disconnected')
		_dispatcher.trigger('ws_close', self)
		WSHandler.clients.discard(self)

	def name(self):
		return getattr(self, 'livestyleClientInfo', {}).get('id', 'unknown')

def on(name, callback):
	_dispatcher.on(name, callback)

def off(name, callback=None):
	_dispatcher.off(name, callback)

def one(name, callback):
	_dispatcher.one(name, callback)

def format_message(msg):
	msg = repr(msg)
	return msg[0:300]

def send(message, client=None, exclude=None):
	"Sends given message to websocket clients"
	if not eutils.isstr(message):
		message = json.dumps(message)
	clients = WSHandler.clients if not client else [client]
	if exclude:
		clients = [c for c in clients if c != exclude]

	if not clients:
		logger.debug('Cannot send message, client list empty')
	else:
		logger.debug('Sending ws message %s' % format_message(message))
		for c in clients:
			c.write_message(message)

def clients():
	return WSHandler.clients

def find_client(flt={}):
	for c in clients():
		info = getattr(c, 'livestyleClientInfo', None)
		if info:
			is_valid = True
			for k,v in flt.items():
				if k == 'supports':
					if v not in info.get('supports', []):
						is_valid = False
						break
				elif info.get(k) != v:
					is_valid = False
					break

			if is_valid:
				return c

		elif not flt:
			return c


application = tornado.web.Application([
	(r'/browser', WSHandler),
	(r'/', LiveStyleIDHandler)
])

def start(port):
	global httpserver
	logger.info('Starting LiveStyle server on port %s' % port)
	httpserver = tornado.httpserver.HTTPServer(application)
	httpserver.listen(port, address='127.0.0.1')
	threading.Thread(target=tornado.ioloop.IOLoop.instance().start).start()

def stop():
	global httpserver
	for c in WSHandler.clients.copy():
		c.close()
	WSHandler.clients.clear()

	if httpserver:
		logger.info('Stopping server')
		httpserver.stop()

	tornado.ioloop.IOLoop.instance().stop()
