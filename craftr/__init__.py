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
''' Craftr is a meta build system for Ninja. '''

__author__ = 'Niklas Rosenstein <rosensteinniklas(at)gmail.com>'
__version__ = '2.0.0-dev'

import sys
if sys.version < '3.4':
  raise EnvironmentError('craftr requires Python3.4')

from craftr import magic

session = magic.new_context('session')
module = magic.new_context('module')

from craftr import ext, path, ninja

import craftr
import types


class Session(object):
  ''' The `Session` object is the manage of a meta build session that
  manages the craftr modules and build `Target`s.

  Attributes:
    path: A list of additional search paths for Craftr modules.
    modules: A dictionary of craftr extension modules, without the
      `craftr.ext.` prefix.
    targets: A dictionary mapping full target names to actual `Target`
      objects that have been created. The `Target` constructors adds
      the object to this dictionary automatically.
    var: A dictionary of variables that will be exported to the Ninja
      build definitions file.
    '''

  def __init__(self):
    super().__init__()
    self.path = [path.join(path.dirname(__file__), 'lib')]
    self.modules = {}
    self.targets = {}
    self.var = {}

  def on_context_enter(self, prev):
    if prev is not None:
      raise RuntimeError('session context can not be nested')

  def on_context_leave(self):
    ''' Remove all `craftr.ext.` modules from `sys.modules` and make
    sure they're all in `Session.modules` (the modules are expected
    to be put there by the `craftr.ext.CraftrImporter`). '''

    for key, module in list(sys.modules.items()):
      if key.startswith('craftr.ext.'):
        name = key[11:]
        assert name in self.modules and self.modules[name] is module, key
        del sys.modules[key]
        try:
          # Remove the module from the `craftr.ext` modules contents, too.
          delattr(ext, name.split('.')[0])
        except AttributeError:
          pass


class Target(object):
  ''' This class is a direct representation of a Ninja rule and the
  corresponding in- and output files that will be built using that rule.

  Attributes:
    name: The name of the target. This is usually deduced from the
      variable the target is assigned to if no explicit name was
      passed to the `Target` constructor. Note that the actualy
      identifier of the target that can be passed to Ninja is
      concatenated with the `module` identifier.
    module: A Craftr extension module which this target belongs to. It
      can be specified on construction manually, or the current active
      module is used automatically.
    command: A list of strings that represents the command to execute.
    inputs: A list of filenames that are listed as direct inputs.
    outputs: A list of filenames that are generated by the target.
    implicit_deps: A list of filenames that mark the target as dirty
      if they changed and will cause it to be rebuilt, but that are
      not taken as direct input files (i.e. `$in` does not expand these
      files).
    order_only_deps: See "Order-only dependencies" in the [Ninja Manual][].
    foreach: A boolean value that determines if the command is appliead
      for each pair of filenames in `inputs` and `outputs`, or invoked
      only once. Note that if this is True, the number of elements in
      `inputs` and `outputs` must match!
    description: A description of the target to display when it is being
      built. This ends up as a variable definition to the target's rule,
      so you may use variables in this as well.
    pool: The name of the build pool. Defaults to None. Can be "console"
      for "targets" that don't actually build files but run a program.
      Craftr ensures that targets in the "console" pool are never
      executed implicitly when running Ninja.  # xxx: todo!
    deps: The mode for automatic dependency detection for C/C++ targets.
      See the "C/C++ Header Depenencies" section in the [Ninja Manual][].
    depfile: A filename that contains additional dependencies.
    msvc_deps_prefix: The MSVC dependencies prefix to be used for the rule.

  [Ninja Manual]: https://ninja-build.org/manual.html
  '''

  def __init__(self, command, inputs, outputs, implicit_deps=None,
      order_only_deps=None, foreach=False, description=None, pool=None,
      var=None, deps=None, depfile=None, msvc_deps_prefix=None,
      module=None, name=None):

    module = TargetBuilder.get_module(module)
    name = TargetBuilder.get_name(module, name)

    if isinstance(command, str):
      command = shell.split(command)
    elif not isinstance(command, list):
      raise TypeError('command: expected {str, list of str}')
    if not command:
      raise ValueError('command: can not be empty')

    if not isinstance(inputs, list):
      raise TypeError('inputs: expected list of str')
    if not inputs:
      raise ValueError('inputs: can not be empty')

    if callable(outputs):
      outputs = outputs(inputs)
      assert not isinstance(outputs, str)
      outputs = list(outputs)
    if not isinstance(outputs, list):
      raise TypeError('outputs: expected list of str')

    if foreach and len(inputs) != len(outputs):
      raise ValueError('len(inputs) must match len(outputs) in foreach Target')

    if implicit_deps is not None and not isinstance(implicit_deps, list):
      raise TypeError('implicit_deps: expected list of str')
    if order_only_deps is not None and not isinstance(order_only_deps, list):
      raise TypeError('order_only_deps: expected list of str')

    self.module = module
    self.name = name
    self.command = command
    self.inputs = inputs
    self.outputs = outputs
    self.implicit_deps = implicit_deps
    self.order_only_deps = order_only_deps
    self.foreach = foreach
    self.pool = pool
    self.description = description
    self.deps = deps
    self.depfile = depfile
    self.msvc_deps_prefix = msvc_deps_prefix

    targets = module.__session__.targets
    if self.fullname in targets:
      raise ValueError('target {0!r} already exists'.format(self.fullname))
    targets[self.fullname] = self

  def __repr__(self):
    pool = ' in "{0}"'.format(self.pool) if self.pool else ''
    command = ' running "{0}"'.format(self.command[0])
    return '<Target {self.fullname!r}{command}{pool}>'.format(**locals())

  @property
  def fullname(self):
    return self.module.__ident__ + '.' + self.name


class TargetBuilder(object):
  ''' Helper class to build a target, used in rule functions. '''

  @staticmethod
  def get_module(ref_module):
    if not ref_module:
      ref_module = module()
    assert isinstance(ref_module, types.ModuleType)
    assert ref_module.__name__.startswith('craftr.ext.')
    return ref_module

  @staticmethod
  def get_name(ref_module, name):
    if not name:
      name = magic.get_assigned_name(magic.get_module_frame(ref_module))
    return name

  def __init__(self, **kwargs):
    super().__init__()
    module = TargetBuilder.get_module(kwargs.pop('module', None))
    name = TargetBuilder.get_name(module, kwargs.pop('name', None))
    self.data = {
      'module': module,
      'name': name,
      'command': [],
      'inputs': [],
      'outputs': [],
      'implicit_deps': [],
      'order_only_deps': [],
      'foreach': False,
      'pool': None,
      'description': None,
      'deps': None,
      'depfile': None,
      'msvc_deps_prefix': None,
    }
    self.data.update(**kwargs)

  def __call__(self, **kwargs):
    self.data.update(**kwargs)
    return Target(**self.data)

  def __getattr__(self, key):
    return self.data[key]

  def __setattr__(self, key, value):
    if key == 'data' or key not in self.data:
      super().__setattr__(key, value)
    else:
      self.data[key] = value

  def warn(self, *args, **kwargs):
    warn("'{0}':".format(self.fullname), *args, **kwargs)


def init_module(module):
  ''' Called when a craftr module is being imported before it is
  executed to initialize its contents. '''

  assert module.__name__.startswith('craftr.ext.')
  module.__ident__ = module.__name__[11:]
  module.__session__ = session()
  module.project_dir = path.dirname(module.__file__)


def expand_inputs(inputs):
  ''' Expands a list of inputs into a list of filenames. An input is a
  string (filename) or a `Target` object from which the `Target.outputs`
  are used. Returns a list of strings. '''

  result = []
  for item in inputs:
    if isinstance(item, Target):
      result += item.outputs
    elif isinstance(item, str):
      result.append(item)
    else:
      raise TypeError('input must be Target or str, got {0}'.format(type(item).__name__))
  return result


def warn(*args, **kwargs):
  print('craftr: warn:', *args, **kwargs)


__all__ = ['craftr', 'session', 'module', 'path', 'Target', 'TargetBuilder']
