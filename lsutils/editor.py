"""
Utility method for Sublime Text editor
"""

import sublime
import sublime_plugin

import re

re_css = re.compile(r'\.css$', re.IGNORECASE)

def main_thread(fn):
	"Run function in main thread"
	return lambda *args, **kwargs: sublime.set_timeout(lambda: fn(*args, **kwargs), 0)

def content(view):
	"Returns content of given view"
	return view.substr(sublime.Region(0, view.size()))

def file_name(view):
	"Returns file name representation for given view"
	return view.file_name()

def view_for_buffer_id(buf_id):
	"Returns view for given buffer id"
	for w in sublime.windows():
		for v in w.views():
			if v.buffer_id() == buf_id:
				return v

def active_view():
	"Returns currently active view"
	return sublime.active_window().active_view()

def view_for_file(path):
	"Locates editor view with given file path"
	for wnd in sublime.windows():
		for view in wnd.views():
			if view.file_name() == path:
				return view

	return None

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

def is_css_view(view):
	return view.file_name() in css_files()

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