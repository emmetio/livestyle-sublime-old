# Dependency graph builder:
# creates dependency graph for given LESS or SCSS source
# and provides a list of dependencies when diffing and patching
# sources 
import time
import zlib
import depreader
import os.path

__cache = {}
__log = False

def crc(file_path):
	content = open(file_path,"rb").read()
	return "%X" % (zlib.crc32(content) & 0xFFFFFFFF)

class Dependency():
	def __init__(self, url):
		self.url = url
		self.crc = None
		self._content = None
		self.check_time = 0
		self.update(time.time())

	def update(self, check_time):
		log = globals()['__log']
		if self.check_time >= check_time:
			# item was already checked and contains most recent data
			if log: print('time guard for %s' % self.url)
			return False

		self.check_time = time.time()
		file_crc = crc(self.url)

		if file_crc == self.crc:
			# file content did't changed since last check
			if log: print('crc guard for %s' % self.url)
			self.validate()
			return False

		self.crc = file_crc
		self.deps = depreader.find_dependencies(self.url, self.content())
		return True

	def content(self):
		if self._content is None:
			self._content = depreader.read(self.url)

		return self._content

	def validate(self):
		"Validates dependencies: make sure they exists"
		if not self.deps:
			return

		deps = []
		for d in self.deps:
			if os.path.isfile(d):
				deps.append(d)

		self.deps = deps

	def release_content(self):
		self._content = None

	def json(self):
		return {
			'url': self.url,
			'crc': self.crc,
			'content': self.content()
		}

def dependencies(url, content, global_deps=[]):
	"Returns plain list of dependencies for given URL and content."
	local_cache = set()
	# in case of circular references, forbid parsing current url
	local_cache.add(url)
	t = time.time()
	result = []

	deps = global_deps + depreader.find_dependencies(url, content)
	for d in deps:
		result += resolve_deps(d, t, local_cache)

	return result

	
def resolve_deps(url, check_time=None, local_cache=None):
	if not url:
		return []

	if check_time is None:
		check_time = time.time()

	if local_cache is None:
		local_cache = set()

	if url in local_cache:
		# item already in cache, no need to parse it
		return []

	local_cache.add(url)

	if url not in __cache:
		__cache[url] = Dependency(url)
	else:
		__cache[url].update(check_time)

	result = [__cache[url]]
	for d in __cache[url].deps:
		result += resolve_deps(d, check_time, local_cache)

	return result

def free_mem(time_delta):
	"Removes old entries from cache, free'ing up some memory"
	t = time.time() - time_delta;

	for k, v in __cache.items():
		if v.check_time < t:
			del __cache[k]

if __name__ == '__main__':
	import sys
	url = sys.argv[-1]
	globals()['__log'] = True

	print('Parsing dependencied for %s' % url)
	content = depreader.read(url)
	print([d.url for d in dependencies(url, content)])

	# test cache
	print([d.url for d in dependencies(url, content)])

	free_mem(-1)
	print('Cached items: %d' % len(__cache))

