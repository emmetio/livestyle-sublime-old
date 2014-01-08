import re
import codecs
import os.path

re_import = re.compile(r'@import\s+(?:\((?:less|css)\)\s*)?(url\(.+?\)|\'.+?\'|".+?")')

def find_dependencies(file_path, content=None):
	"Finds @import dependencies in given file"
	out = []
	if content is None:
		content = read(file_path)
	content = strip_comments(content)
	base = os.path.dirname(file_path)
	for m in re_import.finditer(content):
		url = m.group(1)
		if url.startswith('url('):
			url = url[4:-1]
		if url[0] in '\'"':
			url = url[1:-1]

		url = resolve_url(url, base)
		if url:
			out.append(url)

	return out

def read(file_path):
	with codecs.open(file_path, 'r', 'utf-8') as f:
		return f.read()


def strip_comments(str):
	"Strips comments from given string"
	ranges = []
	stream = enumerate(str)

	def peek(pos):
		if len(str) > pos + 1:
			return str[pos + 1]
		return ''

	for i, ch in stream:
		if ch == '"' or ch == "'":
			skip_string(stream, ch)
			continue

		if ch == '/':
			ch2 = peek(i)
			if ch2 == '*':
				# multiline CSS comment
				start = i
				ix = str.find('*/', i)
				if ix == -1:
					ix = len(str) - 1
				else:
					ix += 2

				# move iterator to given index
				for i, ch in stream:
					if i == ix:
						break

				ranges.append((start, ix))
			elif ch2 == '/':
				# preprocessor's single line comment
				start = i
				for ix, ch in stream:
					if ch == '\n' or ch == '\r':
						break

				ranges.append((start, ix))

	return replace_with(str, ranges, ' ')

def skip_string(stream, quote):
	for i, ch in stream:
		if ch == '\\':
			continue
		if ch == quote:
			return True

	return False

def replace_with(str, ranges, ch):
	"""
	Generic method to replace substrings in given string.
	In Python 2, `unicode` objects are immutable so we can't
	just use str[a:b] = new_substring
	"""
	if not ranges:
		return str

	offset = 0
	out = u''
	fragments = []
	for start, end in ranges:
		out += str[offset:start] + ch * (end - start)
		offset = end

	out += str[offset:]

	return out

def resolve_url(url, base):
	files = [url, u'%s.less' % url]

	if not os.path.isabs(url):
		# not an absolute path, resolve as relative
		for f in files:
			p = os.path.abspath(os.path.join(base, f))
			if os.path.isfile(p):
				return p
		
		# path not found: broken dependency
		return None

	# resolve absolute path intelligently
	files = [f[1:] for f in files]
	parent = base
	prev_parent = None
	while parent and os.path.isdir(parent) and parent != prev_parent:
		for f in files:
			p = os.path.abspath(os.path.join(parent, f))
			if os.path.isfile(p):
				return p

		prev_parent = parent
		parent = os.path.dirname(parent)

	return None
