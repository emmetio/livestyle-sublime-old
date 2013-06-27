"""
Utility functions to scan given Sublime View and build
abstract tree from tokens
"""

import re
import sys

import sublime
import sublime_plugin

is_python3 = sys.version_info[0] > 2

if not is_python3:
	range = xrange

class CSSNode():
	"""A single node in CSS tree"""
	def __init__(self, name_range=None, value_range=None, source='', node_type='section'):
		self.name_range = rt(name_range)
		self.value_range = rt(value_range)
		self.children = []
		self.parent = None
		self.source = source
		self.type = node_type

		self._name = None
		self._value = None

	def add_child(self, node):
		node.parent = self
		self.children.append(node)

	def _substr(self, r):
		return self.source[r[0]:r[1]]

	def name(self):
		if not self._name and self.name_range:
			self._name = self._substr(self.name_range).strip()

		return self._name

	def value(self):
		if not self._value and self.value_range:
			self._value = self._substr(self.value_range).strip()

		return self._value

	def __repr__(self, indent=0):
		name = self.name() or '<root>'
		out = name
		if self.children:
			prefix = '\n' + '\t' * (indent + 1)
			out += ' {' + prefix
			out += prefix.join([c.__repr__(indent + 1) for c in self.children])
			out += '\n}\n'
		elif self.type == 'property':
			out += ': ' + self.value() + ';'
		else: 
			out += ' {}'

		return out


def rt(r):
	"Converts given Sublime Text region into tuple"
	if isinstance(r, sublime.Region):
		return (r.a, r.b)
	if isinstance(r, list):
		return (r[0], r[1])

	return r or None

def view_content(view):
	"Returns content of given view"
	return view.substr(sublime.Region(0, view.size()))

def get_caret_pos(view):
	"Returns caret position in given view"
	return view.sel()[0].a

def print_ranges(view, ranges):
	for r in ranges:
		text = re.sub(r'[\n\r]', '', view.substr(r))
		print('%s: %s' % (r, text))

def property_ranges(prop_range, source):
	"Returns name and value ranges for given property"
	prop = source[prop_range.a:prop_range.b]
	print(prop)
	if not ':' in prop:
		return None, None

	name, value = prop.split(':', 1)

	name_range = (prop_range.a, prop_range.a + len(name))
	value_len = len(value)
	if value[-1] == ';':
		value_len -= 1
	value_range = (name_range[1] + 1, name_range[1] + 1 + value_len)

	return name_range, value_range

def find_sections(view, source=None):
	"Find CSS sections in given view"
	if source is None:
		source = view_content(view)

	ln = len(source)

	selectors = view.find_by_selector('meta.selector')
	# Default CSS highlighter in ST does not support 
	# all latest CSS3 features so we have to manually
	# locate incorrect selectors/at-rules and fix them
	selectors = view.find_by_selector('meta.selector')
	prop_lists = view.find_by_selector('meta.property-list')
	out = []
	for r in selectors:
		if r.a > 0 and source[r.a - 1] == '@':
			# found incorrectly parsed at-rule
			a = r.a - 1
			b = r.b
			while b < ln and source[b] != '{':
				b += 1
			out.append(sublime.Region(a, b))
		else:
			out.append(r)

	return out


def build(view):
	"Builds CSS tree for given view"
	source = view_content(view)

	# associate valid property-value keys with selectors
	selectors = find_sections(view, source)
	props = view.find_by_selector('meta.property-name.css - meta.at-rule, meta.property-value.css - meta.at-rule')
	sel_sections = [CSSNode(r, None, source) for r in selectors]
	
	rule_ix = 0
	for p in props:
		nr, vr = property_ranges(p, source)
		if nr is None:
			continue

		# find best matching selector section for current property.
		# since all list are sorted, we can re-use lookup index for
		# faster search
		found = len(selectors) - 1
		for i in range(rule_ix, len(selectors)):
			if selectors[i].a > p.a:
				found = i - 1
				break

		child = CSSNode(nr, vr, source, 'property')
		sel_sections[found].add_child(child)
		rule_ix = found + 1


	# find @media rules


	print('-- Tree --')
	print('\n'.join([repr(t) for t in sel_sections]))



	# print_ranges(view.find_by_selector('meta.selector.css, meta.property-list.css'))
	# print('-- Selectors --')
	# print_ranges(view, view.find_by_selector('meta.selector'))
	print('-- Prop Section --')
	print_ranges(view, view.find_by_selector('meta.property-list'))
	# print('-- Properties --')
	# print_ranges(view, view.find_by_selector('meta.property-name.css - meta.at-rule, meta.property-value.css - meta.at-rule'))
	

