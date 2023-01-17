#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# node.py

"""
Represents a node in a network. Each node has a unique index, its position in
the network's list of nodes.
"""

import functools

from typing import Optional, Tuple, Union

import numpy as np
import xarray as xr

from .connectivity import get_inputs_from_cm, get_outputs_from_cm
from .constants import SINGLETON_STATE
from .labels import NodeLabels
from .tpm import ExplicitTPM
from .utils import build_state_space, state_of

@xr.register_dataarray_accessor("pyphi")
@functools.total_ordering
class Node:
    """A node in a Network.

    Args:
        dataarray (xr.DataArray):

    Attributes:
        index (int):
        label (str):
        tpm (|ExplicitTPM|): The node TPM is an array with
            |n + 1| dimensions, where ``n`` is the size of the |Network|. The
            first ``n`` dimensions correspond to each node in the
            system. Dimensions corresponding to nodes that provide input to this
            node are of size > 1, while those that do not correspond to inputs are
            of size 1. The last dimension corresponds to the state of the
            node in the next timestep, so that ``node.tpm[..., 0]`` gives
            probabilities that the node will be 'OFF' and ``node.tpm[..., 1]``
            gives probabilities that the node will be 'ON'.
        inputs (frozenset):
        outputs (frozenset):
        state_space (Tuple[Union[int|str]]):
        state (Optional[Union[int|str]]):
    """

    def __init__(self, dataarray: xr.DataArray):
        self._index = dataarray.attrs["index"]

        # Node labels used in the system
        self._node_labels = dataarray.attrs["node_labels"]

        self._inputs = dataarray.attrs["inputs"]
        self._outputs = dataarray.attrs["outputs"]

        self._tpm = dataarray.data

        self.state_space = dataarray.attrs["state_space"]

        # (Optional) current state of this node.
        self.state = dataarray.attrs["state"]

        # Only compute the hash once.
        self._hash = hash(
            (
                self.index,
                hash(self.tpm),
                self._inputs,
                self._outputs,
                self.state_space,
                self.state
            )
        )

    @property
    def index(self):
        """int: The node's index in the network."""
        return self._index

    @property
    def label(self):
        """str: The textual label for this node."""
        return self.node_labels[self.index]

    @property
    def tpm(self):
        """|ExplicitTPM|: The TPM of this node."""
        return self._tpm

    @property
    def inputs(self):
        """frozenset: The set of nodes with connections to this node."""
        return self._inputs

    @property
    def outputs(self):
        """frozenset: The set of nodes this node has connections to."""
        return self._outputs

    @property
    def state_space(self):
        """Tuple[Union[int|str]]: The space of states this node can inhabit."""
        return self._state_space

    @state_space.setter
    def state_space(self, value):
        state_space = tuple(value)

        if len(set(state_space)) < len(state_space):
            raise ValueError(
                "Invalid node state space tuple. Repeated states are ambiguous."
            )

        if len(state_space) < 2:
            raise ValueError(
                "Invalid node state space with less than 2 states."
            )

        self._state_space = state_space

    @property
    def state(self):
        """Optional[Union[int|str]]: The current state of this node."""
        return self._state

    @state.setter
    def state(self, value):
        if value not in self.state_space:
            raise ValueError(
                f"Invalid node state. Possible states are {self.state_space}."
            )

        self._state = value

    def __repr__(self):
        return self.label

    def __str__(self):
        return self.__repr__()

    def __eq__(self, other):
        """Return whether this node equals the other object.

        Two nodes are equal if they have the same index, the same
        inputs and outputs, the same TPM, the same state_space and the
        same state.

        Labels are for display only, so two equal nodes may have different
        labels.

        """
        return (
            self.index == other.index
            and self.tpm.array_equal(other.tpm)
            and self.inputs == other.inputs
            and self.outputs == other.outputs
            and self.state_space == other.state_space
            and self.state == other.state
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    def __lt__(self, other):
        return self.index < other.index

    def __hash__(self):
        return self._hash

    # TODO do we need more than the index?
    def to_json(self):
        """Return a JSON-serializable representation."""
        return self.index


def node(
        tpm: ExplicitTPM,
        cm: np.ndarray,
        network_state_space: Tuple[Tuple[Union[int|str]]],
        index: int,
        state: Optional[Union[int|str]] = None,
        node_labels: Optional[NodeLabels] = None
) -> xr.DataArray:
    """
    Instantiate a node TPM DataArray.

    Args:
        tpm (|ExplicitTPM|): The TPM of this node.
        cm (np.ndarray): The CM of the network.
        network_state_space (Tuple[Tuple[Union[int|str]]]): Labels for the state
            space of each node in the network.
        index (int): The node's index in the network.

    Keyword Args:
        state (Optional[Union[int|str]]): The state of this node.
        node_labels (Optional[|NodeLabels|]): Labels for these nodes.

    Returns:
        xr.DataArray: The node in question.
    """

    # Get indices of the inputs and outputs.
    inputs = frozenset(get_inputs_from_cm(index, cm))
    outputs = frozenset(get_outputs_from_cm(index, cm))

    # Generate DataArray structure for this node
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Dimensions are the names of this node's parents (whose state we
    # can condition this node's TPM on), plus the last dimension with
    # the probability ("Pr") for each possible state of this node in
    # the next timestep.

    # data_vars (xr.DataArray node names) and dimension names share the same
    # dictionary-like namespace in xr.Dataset. Prepend constant "input_" string
    # to avoid the conflict.
    if node_labels is None:
        indices = tuple(range(cm.shape[0]))
        node_labels = NodeLabels(None, indices)

    parent_node_labels = tuple(
        label for dim, label in zip(tpm.shape[:-1], node_labels)
        if dim > 1
    )

    dimensions = ["input_" + label for label in parent_node_labels] + ["Pr"]

    # For each dimension, compute the relevant state labels (coordinates in
    # xarray terminology) from the perspective of this node and its direct
    # inputs.
    state_space, _ = build_state_space(
        tpm.shape[:-1],
        network_state_space,
        singleton_state_space = None,
    )

    node_state_space = network_state_space[index]

    coordinates = [*state_space, node_state_space]

    # TODO(tpm) implement np.result_type() in
    # data_structures.array_like.__array_function__ to avoid converting with
    # np.asarray().
    return xr.DataArray(
        name = node_labels[index],
        data = np.asarray(tpm.squeeze()),
        dims = dimensions,
        coords = list(map(list, coordinates)),
        attrs = {
            "index": index,
            "node_labels": node_labels,
            "inputs": inputs,
            "outputs": outputs,
            "state_space": node_state_space,
            "state": state,
        }
    )


def generate_nodes(
        tpm: ExplicitTPM,
        cm: np.ndarray,
        state_space: Tuple[Tuple[Union[int|str]]],
        indices: Tuple[int],
        network_state: Optional[Tuple[Union[int|str]]] = None,
        node_labels: Optional[NodeLabels] = None
) -> Tuple[xr.DataArray]:
    """Generate |Node| objects out of a binary network |ExplicitTPM|.

    Args:
        tpm (|ExplicitTPM|): The system's TPM.
        cm (np.ndarray): The CM of the network.
        state_space (Tuple[Tuple[Union[int|str]]]): Labels for the state
            space of each node in the network.
        indices (Tuple[int]): Indices to generate nodes for.

    Keyword Args:
        network_state (Optional[Tuple[Union[int|str]]]): The state of the network.
        node_labels (|NodeLabels|): Textual labels for each node.

    Returns:
        Tuple[xr.DataArray]: The nodes of the system.
    """
    if node_labels is None:
        node_labels = NodeLabels(None, indices)

    if network_state is None:
        network_state = (None,) * cm.shape[0]

    node_state = state_of(indices, network_state)

    nodes = []

    for index, state in zip(indices, node_state):
        # Generate the node's TPM.
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # We begin by getting the part of the subsystem's TPM that gives just
        # the state of this node. This part is still indexed by network state,
        # but its last dimension will be gone, since now there's just a single
        # scalar value (this node's state) rather than a state-vector for all
        # the network nodes.
        tpm_on = tpm[..., index]

        # Marginalize out non-input nodes.

        # TODO use names rather than indices
        inputs = frozenset(get_inputs_from_cm(index, cm))
        non_inputs = set(tpm.tpm_indices()) - inputs
        tpm_on = tpm_on.marginalize_out(non_inputs).tpm

        # Get the TPM that gives the probability of the node being off, rather
        # than on.
        tpm_off = 1 - tpm_on

        # Combine the on- and off-TPM so that the first dimension is indexed by
        # the state of the node's inputs at t, and the last dimension is
        # indexed by the node's state at t+1. This representation makes it easy
        # to condition on the node state.
        node_tpm = ExplicitTPM(
            np.stack([tpm_off, tpm_on], axis=-1)
        )
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        nodes.append(
            node(
                node_tpm,
                cm,
                state_space,
                index,
                state=state,
                node_labels=node_labels
            )
        )

    return tuple(nodes)


# TODO: nonbinary nodes
def expand_node_tpm(tpm):
    """Broadcast a node TPM over the full network.

    Args:
        tpm (|ExplicitTPM|): The node TPM to expand.

    This is different from broadcasting the TPM of a full system since the last
    dimension (containing the state of the node) contains only the probability
    of *this* node being on, rather than the probabilities for each node.
    """
    uc = ExplicitTPM(np.ones([2 for node in tpm.shape]))
    return uc * tpm
