"""
Utility method for Sublime Text editor
"""

import sublime
import sublime_plugin

import re

re_css = re.compile(r'\.css$', re.IGNORECASE)
_settings = None

try:
	isinstance("", basestring)
	def isstr(s):
		return isinstance(s, basestring)
except NameError:
	def isstr(s):
		return isinstance(s, str)

def main_thread(fn):
	"Run function in main thread"
	return lambda *args, **kwargs: sublime.set_timeout(lambda: fn(*args, **kwargs), 1)

def get_setting(name, default=None):
	global _settings
	if not _settings:
		_settings = sublime.load_settings('LiveStyle.sublime-settings')

	return _settings.get(name, default)

def parse_json(data):
	return json.loads(data) if isstr(data) else data

def content(view):
	"Returns content of given view"
	return view.substr(sublime.Region(0, view.size()))

def file_name(view):
	"Returns file name representation for given view"
	return view.file_name() or temp_file_name(view)

def temp_file_name(view):
	"Returns temporary name for (unsaved) views"
	return '<untitled:%d>' % view.id()

def all_views():
	"Returns all view from all windows"
	views = []
	for w in sublime.windows():
		for v in w.views():
			views.append(v)

	return views

def view_for_buffer_id(buf_id):
	"Returns view for given buffer id"
	for view in all_views():
		if view.buffer_id() == buf_id:
			return view

	return None

def view_for_file(path):
	"Locates editor view with given file path"
	for view in all_views():
		if file_name(view) == path:
			return view

	return None

def active_view():
	"Returns currently active view"
	return sublime.active_window().active_view()

def css_views():
	"Returns list of opened CSS views"
	return [view for view in all_views() if is_css_view(view)]

def css_files():
	"Returns list of opened CSS files"
	return [file_name(view) for view in css_views()]

def is_css_view(view, strict=False):
	"Check if given view can be used for live CSS"
	sel = get_setting('css_files_selector', 'source.css - source.css.less')
	if not view.file_name() and not strict:
		# For new files, check if current scope is text.plain (just created)
		# or it's a strict CSS
		sel = '%s, text.plain' % sel

	return view.score_selector(0, sel) > 0

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
