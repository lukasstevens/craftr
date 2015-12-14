# Copyright (C) 2015  Niklas Rosenstein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from craftr import path
from itertools import chain

import craftr
import imp
import importlib
import re
import sys
import warnings

# Mark this module as a package to be able to actually import sub
# modules from `craftr.ext`, otherwise the `CraftrImporter` is not
# even invoked at all.
__path__ = []


def get_module_ident(filename):
  ''' Extracts the module identifier from file at the specified
  *filename* and returns it, or None if the file does not contain
  a `craftr_module(...)` declaration in the first comment-block. '''

  if filename.endswith('.craftr'):
    return path.basename(filename)[:-7]

  expr = re.compile('#\s*craftr_module\(([\w\.]+)\)')
  with open(filename, "r") as fp:
    in_comment_block = False
    for line in map(str.rstrip, fp):
      if line.startswith('#'):
        in_comment_block = True
        match = expr.match(line)
        if match:
          return match.group(1)
      elif in_comment_block:
        return False


class CraftrImporter(object):
  ''' Meta-path import hook for importing Craftr modules from the
  `craftr.ext` parent namespace. Only functions inside a session
  context. '''

  def __init__(self, session):
    super().__init__()
    self._cache = {}
    self.session = session

  def _check_file(self, filename):
    if not path.isfile(filename):
      return False
    ident = get_module_ident(filename)
    if not ident:
      message = 'no craftr_module() declaration in "{0}"'.format(filename)
      warnings.warn(message, ImportWarning)
      return False
    if ident in self._cache and self._cache[ident] != filename:
      message ='module "{0}" already found elsewhere'.format(ident)
      warnings.warn(message, ImportWarning)
      return False
    self._cache[ident] = filename
    return True

  def _rebuild_cache(self):
    ''' Rebuilds the importer cache for craftr modules. '''

    def check_dir(dirname):
      self._check_file(path.join(dirname, 'Craftfile'))
      for filename in path.listdir(dirname):
        if filename.endswith('.craftr'):
          self._check_file(filename)

    self._cache.clear()
    for dirname in map(path.normpath, self.session.path):
      if not path.isdir(dirname):
        continue
      check_dir(dirname)
      # Also check second-level directories.
      for subdir in path.listdir(dirname):
        if path.isdir(subdir):
          check_dir(subdir)

  def _get_module_info(self, fullname):
    ''' Returns a tuple that contains information about a craftr module
    with the specified *fullname*. Either a namespace module or a real
    module can be loaded from this information.

    The return *type* is either `None`, `'namespace'` or `'module'`.
    The *filename* is only set when the *type* is `'module'`.

    Returns:
      tuple: `(type, filename)`
    '''

    if fullname in self._cache:
      return ('module', self._cache[fullname])
    fullname += '.'
    for key in self._cache.keys():
      if key.startswith(fullname):
        return ('namespace', None)
    return (None, None)

  def update(self):
    ''' Should be called if `sys.path` or `Session.path` has been
    changed to rebuild the module cache and delay-load virtual modules
    if a physical was found. '''

    assert craftr.session == self.session

    self._rebuild_cache()
    for key, module in list(self.session.modules.items()):
      # Virtual modules have no __file__ member.
      if not hasattr(module, '__file__'):
        kind = self._get_module_info(key)[0]
        assert kind in (None, 'namespace', 'module'), kind
        if kind == 'module':
          # xxx: I feel like this is a very dirty solution. Maybe
          # we should only need to reload the module but use proxies
          # everywhere so you don't have a virtual and physical copy
          # of the module floating around.
          module = importlib.reload(module)
          parent, _, name = key.rpartition('.')
          if parent:
            setattr(self.session.modules[parent], name, module)

  def find_module(self, fullname, path=None):
    assert craftr.session == self.session
    if not fullname.startswith('craftr.ext.'):
      return None

    # xxx: take the *path* argument into account?
    name = fullname[11:]
    kind, filename = self._get_module_info(name)
    if kind:
      return CraftrLoader(kind, filename, self.session)
    return None


class CraftrLoader(object):

  def __init__(self, kind, filename, session):
    super().__init__()
    self.kind = kind
    self.filename = filename
    self.session = session

  def load_module(self, fullname):
    assert fullname.startswith('craftr.ext.')
    assert craftr.session == self.session
    name = fullname[11:]

    assert self.kind and self.kind in ('namespace', 'module')
    module = imp.new_module(fullname)
    module.__path__ = []
    sys.modules[fullname] = module
    self.session.modules[name] = module
    if self.kind == 'module':
      module.__file__ = self.filename
      try:
        craftr.init_module(module)
        with craftr.magic.enter_context(craftr.module, module):
          try:
            with open(self.filename, 'r') as fp:
              exec(compile(fp.read(), self.filename, 'exec'), vars(module))
          finally:
            craftr.finish_module(module)
      except Exception:
        del sys.modules[fullname]
        del self.session.modules[name]
        raise

    return module