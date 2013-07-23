# PyV8 loader delegate: loads PyV8 bundle 
# and displays loading progress
import sublime

import sys
import os.path
import logging
import imp

import lsutils.pyv8loader as pyv8loader

logger = logging.getLogger('livestyle')

BASE_PACKAGES_PATH = os.path.normpath( os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Packages') )
PACKAGES_PATH = sublime.packages_path() or BASE_PACKAGES_PATH
PYV8_PATHS = [
	os.path.join(PACKAGES_PATH, 'PyV8'),
	os.path.join(PACKAGES_PATH, 'PyV8', pyv8loader.get_arch()),
	os.path.join(PACKAGES_PATH, 'PyV8', 'pyv8-%s' % pyv8loader.get_arch())
]

# unpack recently loaded binary, is exists
for p in PYV8_PATHS:
	pyv8loader.unpack_pyv8(p)

class LoaderDelegate(pyv8loader.LoaderDelegate):
	def __init__(self, callback=None, settings={}):
		# Use Package Control settings
		pc_settings = sublime.load_settings('Package Control.sublime-settings')
		for k in ['http_proxy', 'https_proxy', 'timeout']:
			if pc_settings.has(k):
				settings[k] = pc_settings.get(k, None)

		pyv8loader.LoaderDelegate.__init__(self, settings)
		self.callback = callback
		self.state = None
		self.message = 'Loading PyV8 binary, please wait'
		self.i = 0
		self.addend = 1
		self.size = 8

	def on_start(self, *args, **kwargs):
		self.state = 'loading'

	def on_progress(self, *args, **kwargs):
		if kwargs['progress'].is_background:
			return
			
		before = self.i % self.size
		after = (self.size - 1) - before
		msg = '%s [%s=%s]' % (self.message, ' ' * before, ' ' * after)
		if not after:
			self.addend = -1
		if not before:
			self.addend = 1
		self.i += self.addend

		sublime.set_timeout(lambda: sublime.status_message(msg), 0)

	def on_complete(self, *args, **kwargs):
		self.state = 'complete'
		def _c():
			sublime.status_message('PyV8 binary successfully loaded')
			if self.callback:
				self.callback(True)

		sublime.set_timeout(_c, 0)

	def on_error(self, *args, **kwargs):
		self.state = 'error'
		def _c():
			if 'exit_code' in kwargs:
				logger.error('Error while loading PyV8 binary: exit code %s \nTry to manually install PyV8 from\nhttps://github.com/emmetio/pyv8-binaries' % kwargs.get('exit_code', -1))
			
			if self.callback:
				self.callback(False)

		sublime.set_timeout(_c, 0)

	def setting(self, name, default=None):
		"Returns specified setting name"
		return self.settings.get(name, default)

	def log(self, message):
		logger.info(message)