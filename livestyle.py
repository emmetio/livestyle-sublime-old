import json
import re
import threading
import sys
import os.path
import imp

import sublime
import sublime_plugin

import tornado.httpserver
import tornado.websocket
import tornado.ioloop
import tornado.web

BASE_PATH = os.path.abspath(os.path.dirname(__file__))
PACKAGES_PATH = sublime.packages_path() or os.path.dirname(BASE_PATH)
sys.path += [
	BASE_PATH, 
	os.path.join(BASE_PATH, 'tornado'),
	os.path.join(PACKAGES_PATH, 'Emmet'),
	os.path.join(PACKAGES_PATH, 'emmet-sublime')
]

try:
	import emmet.context
except ImportError:
	print('Unable to find Emmet package')
	raise

sublime_ver = sublime.version()[0]
editor = {
	'id': 'st%s' % sublime_ver,
	'title': 'Sublime Text %s' % sublime_ver,
	'ctx': None,
	'active': False
}

re_css = re.compile(r'\.css$', re.IGNORECASE)
CSS_SECTION_SEL = 'source.css meta.property-list.css'
is_python3 = sys.version_info[0] > 2

_cache = {
	'supress_modification': False,
	'file': None,
	'section_pos': -1,
	'props': []
}

_patching_state = {
	'pending': [],
	'timer_active': False
}

try:
	isinstance("", basestring)
	def isstr(s):
		return isinstance(s, basestring)
except NameError:
	def isstr(s):
		return isinstance(s, str)

class EmmetContextError(Exception):
	def __str__(self):
		return "Emmet JS context is not available. Make sure you have the most recent Emmet plugin and PyV8 package"

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
			update_css(message['data'])
			send_message(message, exclude=self)

	def on_close(self):
		print('connection closed')
		WSHandler.clients.discard(self)

def assert_ctx():
	if not editor['ctx']:
		raise EmmetContextError()

def main_thread(fn):
	"Run function in main thread"
	return lambda *args, **kwargs: sublime.set_timeout(lambda: fn(*args, **kwargs), 0)


@main_thread
def request_patching(payload):
	patch = editor['ctx'].js().locals.livestyle.makePatch(json.dumps(payload))
	if patch:
		_patching_state['pending'].append(patch)

	if not _patching_state['timer_active']:
		def callback():
			patches = json.dumps(_patching_state['pending'])
			_patching_state['pending'] = []
			_patching_state['timer_active'] = False

			upd = editor['ctx'].js().locals.livestyle.makeUpdatePayload(patches)
			if upd:
				print(upd)
				send_message(upd)

		_patching_state['timer_active'] = True
		sublime.set_timeout(callback, 500)

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


def css_views():
	"Returns list of opened CSS views"
	files = []

	for wnd in sublime.windows():
		for view in wnd.views():
			if re_css.search(view.file_name() or ''):
				files.append(view)

	return files

def css_files():
	"Returns list of opened CSS files"
	return [view.file_name() for view in css_views()]

@main_thread
def identify_editor(socket):
	"Sends editor identification info to browser"
	send_message({
		'action': 'id',
		'data': {
			'id': editor['id'],
			'title': editor['title'],
			'icon': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABu0lEQVR42q2STWsTURhG3WvdCyq4CEVBAgYCM23JjEwy+cJC41gRdTIEGyELU7BNNMJQhUBBTUjSRdRI3GThRld+gbj2JwhuRFy5cZ3Ncd5LBwZCIIIXDlzmeZ9z4d458t9WoVB4XywWCcnn89i2TSaTIZvNEuRhJvtP0e7R6XT6VYJer8dkMmE0GrHf3uPxg1s8f+TR9ncZDocq63a7SiId6YogBqiPg8FASe43d3iz7/D7rcuP1zf4NnHxfV9yQc0CSFcEeihotVo0Gg22tzbh3SbP7lq4lzTuuHlqtZrkQlSgi8AIBZVKBc/zuH5lnc7tFX4OL/L9wOTJlsbGepFyuSwzUYERCqIXhGVZJJNJbqbP0b66DC8ucO/yedLptMzMF4S3X7JXeFWJ4Zln2LZPw9NT+BuxxQTquaw1Xl47yZ/WEr92j3PgnMBc08nlcvMF1Wo1DNW7G4aBpmnouo5pmtGyzM4K+v0+4/F4ITqdzqzAdV0cxyGVSsmpc5G/s1QqzQg+N5tNdUmJRIJ4PD4XkdTrdaQTClYDlvnHFXTOqu7h5mHAx4AvC/IhYE+6IliK2IwFWT3sHPsL6BnLQ4kfGmsAAAAASUVORK5CYII=',
			'files': css_files()
		}
	}, socket)

@main_thread
def update_files():
	send_message({
		'action': 'updateFiles',
		'data': css_files()
	})

def find_file_view(path):
	"Locates editor view with given file path"
	for wnd in sublime.windows():
		for view in wnd.views():
			if view.file_name() == path:
				return view

	return None

def unindent_text(text, pad):
	"""
	Removes padding at the beginning of each text's line
	@type text: str
	@type pad: str
	"""
	lines = text.splitlines()
	
	for i,line in enumerate(lines):
		if line.startswith(pad):
			lines[i] = line[len(pad):]
	
	return '\n'.join(lines)

def get_line_padding(line):
	"""
	Returns padding of current editor's line
	@return str
	"""
	m = re.match(r'^(\s+)', line)
	return m and m.group(0) or ''

@main_thread
def update_css(payload):
	assert_ctx()

	print('Updating CSS')

	editor_file = payload.get('editorFile')
	if not editor_file:
		print('No editor file')
		return

	view = find_file_view(editor_file)
	if view is None:
		print('Unable to find view for %s file' % payload['file'])
		return 

	content = view_content(view)
	upd = editor['ctx'].js().locals.livestyle.updatedPart(content, json.dumps(payload))
	if upd:
		sublime.active_window().focus_view(view)
		sel = None
		for r in reversed(json.loads(upd)):
			view.sel().clear()
			view.sel().add(sublime.Region(r[0], r[1]))
			value = r[2]

			if not is_python3:
				value = value.decode('utf-8')

			_cache['supress_modification'] = True
			_cache['file'] = None
			line = view.substr(view.line(r[0]))
			value = unindent_text(value, get_line_padding(line))
			view.run_command('insert_snippet', {'contents': value})
			sel = sublime.Region(r[0], r[0] + len(value))
			
		if sel:
			view.sel().clear()
			view.sel().add(sel)
			view.show(sel)

def view_content(view):
	return view.substr(sublime.Region(0, view.size()))

def diff_props(pl1, pl2):
	"Compares two CSS properties list and returns diff"
	added = []
	updated = []
	removed = []

	if not is_python3:
		range = xrange

	pl1_len = len(pl1)
	pl2_len = len(pl2)

	lookup_ix = 0
	for p in pl2:
		if lookup_ix >= pl1_len:
			added.append(p)
			continue

		op = pl1[lookup_ix]
		if p[0] == op[0]:
			lookup_ix += 1
			if p[1] != op[1]:
				# same property, different value:
				# the property was updated
				# TODO check for edge cases with vendor-prefixed values
				updated.append(p)
			continue

		# look further for property with the same name
		did_removed = False
		for next_ix in range(lookup_ix + 1, pl1_len):
			op = pl1[next_ix]
			if p[0] == op[0] and p[1] == op[1]:
				removed += pl1[lookup_ix:next_ix]
				lookup_ix = next_ix + 1
				did_removed = True
				break

		if not did_removed:
			added.append(p)
		
	if lookup_ix < pl1_len:
		removed += pl1[lookup_ix:]

	return added, updated, removed

def get_caret_pos(view):
	return view.sel()[0].a

def cur_css_section_range(view, caret=None):
	if caret is None:
		caret = get_caret_pos(view)

	for r in view.find_by_selector(CSS_SECTION_SEL):
		if r.contains(caret):
			return r

def view_file_name(view):
	return view.file_name()

@main_thread
def send_updates(payload):
	"Sends updated CSS data to browser"
	assert_ctx()

	upd = editor['ctx'].js().locals.livestyle.makePatch(json.dumps(payload))
	if upd:
		print(upd)
		send_message(upd)


def get_css_props(view, rng=None, caret=None):
	"""
	Returns list of CSS properties for given caret position.
	If caret is outside of CSS rule, returns None
	"""
	if rng is None:
		if caret is None:
			caret = get_caret_pos(view)
		if not view.score_selector(caret, CSS_SECTION_SEL):
			return None
		rng = cur_css_section_range(view)

	all_props = [view.substr(r) for r in view.find_by_selector('meta.property-name.css, meta.property-value.css') if rng.contains(r)]
	props = []
	for i, s in enumerate(all_props):
		if ':' in s:
			name, value = transform_css_property(s)
			props.append((name, value, i))

	return props


def transform_css_property(prop):
	"Transforms property, extracted from CSS source, into a name-value tuple"
	parts = prop.split(':')
	name = parts.pop(0).strip()
	value = ':'.join(parts).strip()
	if value[-1] == ';':
		value = value[0:-1].strip()

	return name, value

def should_handle(view):
	# don't do anything if there are no connected clients
	# or change performed outside of CSS file
	return WSHandler.clients and view.file_name() in css_files()

class LiveStyleListener(sublime_plugin.EventListener):
	def on_load(self, view):
		update_files()

	def on_close(self, view):
		update_files()

	def on_modified(self, view):
		if not should_handle(view):
			return

		caret = get_caret_pos(view)
		if view.score_selector(caret, CSS_SECTION_SEL):
			if _cache['supress_modification']:
				_cache['supress_modification'] = False
				return False

			cur_section = cur_css_section_range(view)
			if _cache['file'] == view.file_name() \
				and _cache['section_pos'] == cur_section.a:
				props = get_css_props(view, rng=cur_section)
				added, updated, removed = diff_props(_cache['props'] or [], props)

				# print('Added: %s, Updated: %s, Removed: %s' % (added, updated, removed))
				_cache['props'] = props
				
				if not added and not updated and not removed:
					# nothing to update
					return

				request_patching({
					'url': view_file_name(view),
					'content': view_content(view),
					'caret': caret,
					'added': added,
					'updated': updated,
					'removed': removed
				})

	def on_selection_modified(self, view):
		if not should_handle(view):
			return

		caret = get_caret_pos(view)
		if view.score_selector(caret, CSS_SECTION_SEL):
			cur_section = cur_css_section_range(view)
			if _cache['file'] != view.file_name() \
				or _cache['section_pos'] != cur_section.a:
				# print('store props')
				_cache['file'] = view.file_name()
				_cache['section_pos'] = cur_section.a
				_cache['props'] = get_css_props(view, rng=cur_section)

	def on_activated(self, view):
		self.on_selection_modified(view)


# XXX init
application = tornado.web.Application([
	(r'/browser', WSHandler),
])

def start_server(port, ctx=None):
	print('Starting LiveStyle server on port %s' % port)
	editor['httpserver'] = tornado.httpserver.HTTPServer(application)
	editor['httpserver'].listen(port, address='127.0.0.1')
	threading.Thread(target=tornado.ioloop.IOLoop.instance().start).start()
	editor['active'] = True

def stop_server():
	editor['active'] = False

	for c in WSHandler.clients.copy():
		c.close()
	WSHandler.clients.clear()

	server = editor.get('httpserver', None)
	if server:
		server.stop()

	tornado.ioloop.IOLoop.instance().stop()

def unload_handler():
	if 'emmet.context' in sys.modules:
		emmet.context.remove_reload_callback(add_context_callback)
	
	stop_server()
	editor['ctx'] = None

def reload_context():
	if 'emmet.context' in sys.modules:
		reload(sys.modules['emmet.context'])
	sublime.set_timeout(add_context_callback, 0)

def add_context_callback():
	editor['ctx'] = None
	emmet.context.on_context_created(init_context)
	emmet.context.on_context_reload(reload_context)

def init_context(ctx):
	js_file = os.path.join(BASE_PATH, 'livestyle-src.js')
	if not os.path.exists(js_file):
		js_file = os.path.join(BASE_PATH, 'livestyle.js')


	ctx.eval_js_file(js_file)
	editor['ctx'] = ctx

def start_plugin():
	add_context_callback()
	start_server(54000)

# Init plugin
def plugin_loaded():
	sublime.set_timeout(start_plugin, 200)

if not is_python3:
	start_plugin()