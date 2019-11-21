# Copyright 2019 The DDSP Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Library to string Processors together in a ProcessorGroup.

This library exists as an alternatively to
manually specifying the forward propagation in python. The advantage is that a
variety of configurations can be programmatically specified via external
dependency injection, such as with the `gin` library. Examples can be found in
processor_group_test.py and training examples in ddsp/training/model.py.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from typing import Dict, Mapping, Sequence, Tuple, Text, TypeVar, Union

from absl import logging
from ddsp import core
import gin
import tensorflow.compat.v1 as tf

TensorDict = Dict[Text, tf.Tensor]


# Processor Base Class ---------------------------------------------------------
class Processor(object):
  """Abstract base class for signal processors.

  Since most effects / synths require specificly formatted control signals
  (such as amplitudes and frequenices), each processor implements a
  get_controls(inputs) method, where inputs are a variable number of tensor
  arguments that are typically neural network outputs. Check each child class
  for the class-specific arguments it expects. This gives a dictionary of
  controls that can then be passed to get_signal(controls). The
  get_outputs(inputs) method calls both in succession and returns a nested
  output dictionary with all controls and signals.
  """

  def __init__(self, name: Text):
    self.name = name

  def __call__(self,
               *args: tf.Tensor,
               **kwargs: tf.Tensor) -> tf.Tensor:
    """Convert input tensors arguments into a signal tensor."""
    controls = self.get_controls(*args, **kwargs)
    signal = self.get_signal(**controls)
    return signal

  def get_outputs(self,
                  *args: tf.Tensor,
                  **kwargs: tf.Tensor) -> TensorDict:
    """Get outputs dictionary from input tensor arguments."""
    controls = self.get_controls(*args, **kwargs)
    signal = self.get_signal(**controls)
    outputs = {self.name: {'controls': controls, 'signal': signal}}
    return outputs

  def get_controls(self,
                   *args: tf.Tensor,
                   **kwargs: tf.Tensor) -> TensorDict:
    """Convert input tensor arguments into a dict of processor controls."""
    raise NotImplementedError

  def get_signal(self,
                 *args: tf.Tensor,
                 **kwargs: tf.Tensor) -> tf.Tensor:
    """Convert control tensors into a signal tensor."""
    raise NotImplementedError


# ProcessorGroup Class ---------------------------------------------------------

# Define Types.
NodeAsTuple = Tuple[Processor, Sequence[Text]]
NodeAsDict = Mapping[Text, Union[Processor, Sequence[Text]]]
Node = TypeVar('Node', NodeAsTuple, NodeAsDict)
DAG = Sequence[Node]


@gin.configurable
class ProcessorGroup(Processor):
  """String Proccesor() objects together into a processor_group."""

  def __init__(self,
               dag: DAG,
               name: Text = 'processor_group'):
    """Constructor.

    Args:
      dag: A directed acyclical graph in the form of an iterable of tuples or
          dictionaries. Tuples are intepreted as (processor, [inputs]), or
          dictionaries need to be of the form {"processor": , "inputs": []}.
          "Processor" should be an instance of a Processor() object.

          "Inputs" is an iterable of strings corresponding to the nested key of
          a processor output dictionary returned from processor.get_outputs().
          For example, "synth_additive/controls/f0_hz" would correspond to the
          value {"synth_additive": {"controls": {"f0_hz": value}}}.

          The graph is read sequentially and must be topologically sorted. This
          means that all inputs for a processor must already be generated by
          earlier processors (or inputs to the processor_group).
      name: Name of processor_group.
    """
    super(ProcessorGroup, self).__init__(name=name)
    self.dag = dag

  @property
  def _output_node_name(self):
    """Get output signal from last processor."""
    processor, _ = self._parse_node(self.dag[-1])
    return processor.name

  def _parse_node(self, node: Node) -> NodeAsTuple:
    """Read a node in the DAG.

    Args:
      node: Node in the DAG.

    Returns:
      processor: The Processor of the node.
      input_strings: List of nested key strings.

    Raises:
      ValueError: If node is not of type Node.
    """
    if isinstance(node, dict):
      processor, input_strings = node['processor'], node['inputs']
    elif isinstance(node, Sequence):
      processor, input_strings = node[0], node[1]
    else:
      raise ValueError('Nodes of the DAG must either have the form, '
                       '(processor, [input_strings]), or '
                       '{"processor": processor, "inputs": [input_strings]}.')
    return processor, input_strings

  def __call__(self, dag_inputs):
    return self.get_outputs(dag_inputs)

  def get_signal(self, dag_inputs):
    outputs = self.get_outputs(dag_inputs)
    return outputs[self.name]['signal']

  def get_controls(self, *args, **kwargs):
    raise NotImplementedError('ProcessorGroups do not have control outputs.')

  def _get_input_from_string(self,
                             input_string: Text,
                             outputs_dict: TensorDict,) -> tf.Tensor:
    """Returns the value of a nested dict according to a parsed input string.

    Args:
      input_string: String of the form "key/key/key...".
      outputs_dict: Nested dictionary with all processor outputs so far.

    Returns:
      value: Value of the key from the nested outputs dictionary.
    """
    # Parse the input string.
    input_keys = input_string.split('/')
    # Return the nested value.
    value = outputs_dict
    for key in input_keys:
      value = value[key]
    return value

  def get_outputs(self, dag_inputs: TensorDict) -> TensorDict:
    """Run the DAG and get complete outputs dictionary for the processor_group.

    Args:
      dag_inputs: A dictionary of input tensors fed to the signal
        processing processor_group.

    Returns:
      outputs: A nested dictionary of all the output tensors.
    """
    # Initialize the outputs with inputs to the processor_group.
    outputs = dag_inputs

    # Run through the DAG nodes in sequential order.
    for node in self.dag:
      # Get the node processor.
      processor, input_strings = self._parse_node(node)

      # Logging.
      logging.info('Connecting node (%s):', processor.name)
      for i, input_str in enumerate(input_strings):
        logging.info('Input %d: %s', i, input_str)

      # Get the inputs to the node.
      inputs = [self._get_input_from_string(input_str, outputs)
                for input_str in input_strings]

      # Run processor and add outputs to the dictionary.
      outputs.update(processor.get_outputs(*inputs))

    # Get output signal from last processor.
    outputs.update(
        {self.name: {'signal': outputs[self._output_node_name]['signal']}})

    # Logging.
    logging.info('ProcessorGroup output node (%s)', self._output_node_name)

    return outputs


# Routing processors for manipulating signals in a processor_group -------------
@gin.configurable
class Add(Processor):
  """Sum two signals."""

  def __init__(self, name: Text = 'add'):
    super(Add, self).__init__(name=name)

  def get_controls(self,
                   signal_one: tf.Tensor,
                   signal_two: tf.Tensor) -> TensorDict:
    """Just pass signals through."""
    return {'signal_one': signal_one, 'signal_two': signal_two}

  def get_signal(self,
                 signal_one: tf.Tensor,
                 signal_two: tf.Tensor) -> tf.Tensor:
    return signal_one + signal_two


@gin.configurable
class Split(Processor):
  """Split a tensor into multiple signals."""

  def __init__(self,
               splits: Sequence[Tuple[Text, int]],
               name: Text = 'split'):
    super(Split, self).__init__(name=name)
    self.labels = [out[0] for out in splits]
    self.sizes = [out[1] for out in splits]

  def get_signal(self, signal: tf.Tensor) -> Sequence[tf.Tensor]:
    """Split along the last dimension."""
    return tf.split(signal, self.sizes, axis=-1)

  def get_outputs(self,
                  *args: tf.Tensor,
                  **kwargs: tf.Tensor) -> TensorDict:
    """Label signal splits increasing from."""
    signals = self.get_signal(*args, **kwargs)
    signal_dict = {k: v for k, v in zip(self.labels, signals)}
    outputs = {self.name: {'signal': signal_dict}}
    return outputs


@gin.configurable
class Mix(Processor):
  """Constant-power crossfade between two signals."""

  def __init__(self,
               name: Text = 'mix'):
    super(Mix, self).__init__(name=name)

  def get_controls(self,
                   signal_one: tf.Tensor,
                   signal_two: tf.Tensor,
                   nn_out_mix_level: tf.Tensor) -> TensorDict:
    """Standardize inputs to same length, mix_level to range [0, 1].

    Args:
      signal_one: 2-D or 3-D tensor.
      signal_two: 2-D or 3-D tensor.
      nn_out_mix_level: Tensor of shape [batch, n_time, 1] output of the network
          determining relative levels of signal one and two.

    Returns:
      Dict of control parameters.

    Raises:
      ValueError: If signal_one and signal_two are not the same length.
    """
    n_time_one = signal_one.get_shape().as_list()[1]
    n_time_two = signal_two.get_shape().as_list()[1]
    if n_time_one != n_time_two:
      raise ValueError('The two signals must have the same length instead of'
                       '{} and {}'.format(n_time_one, n_time_two))

    mix_level = tf.nn.sigmoid(nn_out_mix_level)
    mix_level = core.resample(mix_level, n_time_one)
    return {'signal_one': signal_one,
            'signal_two': signal_two,
            'mix_level': mix_level}

  def get_signal(self,
                 signal_one: tf.Tensor,
                 signal_two: tf.Tensor,
                 mix_level: tf.Tensor) -> tf.Tensor:
    """Constant-power cross fade between two signals.

    Args:
      signal_one: 2-D or 3-D tensor.
      signal_two: 2-D or 3-D tensor.
      mix_level: Tensor of shape [batch, n_time, 1] determining relative levels
          of signal one and two. Must have same number of time steps as the
          other signals and be in the range [0, 1].

    Returns:
      Tensor of mixed output signal.
    """
    mix_level_one = tf.sqrt(tf.abs(mix_level))
    mix_level_two = 1.0 - tf.sqrt(tf.abs(mix_level - 1.0))
    return mix_level_one * signal_one + mix_level_two * signal_two

