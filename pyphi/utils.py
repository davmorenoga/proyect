#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# utils.py

"""
Functions used by more than one PyPhi module or class, or that might be of
external use.
"""

import os
import hashlib
from itertools import chain, combinations, product

import numpy as np
from scipy.misc import comb
from scipy.sparse import csc_matrix

from . import constants, convert
from .cache import cache


def state_of(nodes, network_state):
    """Return the state-tuple of the given nodes."""
    return tuple(network_state[n] for n in nodes) if nodes else ()


def all_states(n, holi=False):
    """Return all binary states for a system.

    Args:
        n (int): The number of elements in the system.
        holi (bool): Whether to return the states in HOLI order instead of LOLI
            order.

    Yields:
        tuple[int]: The next state of an ``n``-element system, in LOLI order
            unless ``holi`` is ``True``.
    """
    if n == 0:
        return

    for state in product((0, 1), repeat=n):
        if holi:
            yield state
        else:
            yield state[::-1]  # Convert to LOLI-ordering


# Methods for converting the time scale of the tpm
# ================================================

def sparse(matrix, threshold=0.1):
    return np.sum(matrix > 0) / matrix.size > threshold


def sparse_time(tpm, time_scale):
    sparse_tpm = csc_matrix(tpm)
    return (sparse_tpm ** time_scale).toarray()


def dense_time(tpm, time_scale):
    return np.linalg.matrix_power(tpm, time_scale)


def run_tpm(tpm, time_scale):
    """Iterate a TPM by the specified number of time steps.

    Args:
        tpm (np.ndarray): A state-by-node tpm.
        time_scale (int): The number of steps to run the tpm.

    Returns:
        np.ndarray
    """
    sbs_tpm = convert.state_by_node2state_by_state(tpm)
    if sparse(tpm):
        tpm = sparse_time(sbs_tpm, time_scale)
    else:
        tpm = dense_time(sbs_tpm, time_scale)
    return convert.state_by_state2state_by_node(tpm)


def run_cm(cm, time_scale):
    """Iterate a connectivity matrix the specified number of steps.

    Args:
        cm (np.ndarray): A |N x N| connectivity matrix
        time_scale (int): The number of steps to run.

    Returns:
        np.ndarray
    """
    cm = np.linalg.matrix_power(cm, time_scale)
    # Round non-unitary values back to 1
    cm[cm > 1] = 1
    return cm


# TPM and Connectivity Matrix utils
# ============================================================================

def state_by_state(tpm):
    """Return ``True`` if ``tpm`` is in state-by-state form, otherwise
    ``False``."""
    return tpm.ndim == 2 and tpm.shape[0] == tpm.shape[1]


def condition_tpm(tpm, fixed_nodes, state):
    """Return a TPM conditioned on the given fixed node indices, whose states
    are fixed according to the given state-tuple.

    The dimensions of the new TPM that correspond to the fixed nodes are
    collapsed onto their state, making those dimensions singletons suitable for
    broadcasting. The number of dimensions of the conditioned TPM will be the
    same as the unconditioned TPM.
    """
    conditioning_indices = [[slice(None)]] * len(state)
    for i in fixed_nodes:
        # Preserve singleton dimensions with `np.newaxis`
        conditioning_indices[i] = [state[i], np.newaxis]
    # Flatten the indices.
    conditioning_indices = list(chain.from_iterable(conditioning_indices))
    # Obtain the actual conditioned TPM by indexing with the conditioning
    # indices.
    return tpm[conditioning_indices]


def expand_tpm(tpm):
    """Broadcast a state-by-node TPM so that singleton dimensions are expanded
    over the full network."""
    uc = np.ones([2] * (tpm.ndim - 1) + [tpm.shape[-1]])
    return tpm * uc


def apply_boundary_conditions_to_cm(external_indices, cm):
    """Return a connectivity matrix with all connections to or from external
    nodes removed.
    """
    cm = cm.copy()
    for i in external_indices:
        # Zero-out row
        cm[i] = 0
        # Zero-out column
        cm[:, i] = 0
    return cm


def get_inputs_from_cm(index, cm):
    """Return a tuple of node indices that have connections to the node with
    the given index.
    """
    return tuple(i for i in range(cm.shape[0]) if cm[i][index])


def get_outputs_from_cm(index, cm):
    """Return a tuple of node indices that the node with the given index has
    connections to.
    """
    return tuple(i for i in range(cm.shape[0]) if cm[index][i])


def causally_significant_nodes(cm):
    """Return a tuple of all nodes indices in the connectivity matrix which
    are causally significant (have inputs and outputs)."""
    inputs = cm.sum(0)
    outputs = cm.sum(1)
    nodes_with_inputs_and_outputs = np.logical_and(inputs > 0, outputs > 0)
    return tuple(np.where(nodes_with_inputs_and_outputs)[0])


def np_immutable(a):
    """Make a NumPy array immutable."""
    a.flags.writeable = False
    return a


def np_hash(a):
    """Return a hash of a NumPy array."""
    if a is None:
        return hash(None)
    # Ensure that hashes are equal whatever the ordering in memory (C or
    # Fortran)
    a = np.ascontiguousarray(a)
    # Compute the digest and return a decimal int
    return int(hashlib.sha1(a.view(a.dtype)).hexdigest(), 16)


def phi_eq(x, y):
    """Compare two phi values up to |PRECISION|."""
    return abs(x - y) <= constants.EPSILON


def normalize(a):
    """Normalize a distribution.

    Args:
        a (np.ndarray): The array to normalize.

    Returns:
        np.ndarray: ``a`` normalized so that the sum of its entries is 1.
    """
    sum_a = a.sum()
    if sum_a == 0:
        return a
    return a / sum_a


# see http://stackoverflow.com/questions/16003217
def combs(a, r):
    """NumPy implementation of itertools.combinations.

    Return successive |r|-length combinations of elements in the array ``a``.

    Args:
        a (np.ndarray): The array from which to get combinations.
        r (int): The length of the combinations.

    Returns:
        np.ndarray: An array of combinations.
    """
    # Special-case for 0-length combinations
    if r == 0:
        return np.asarray([])

    a = np.asarray(a)
    data_type = a.dtype if r == 0 else np.dtype([('', a.dtype)] * r)
    b = np.fromiter(combinations(a, r), data_type)
    return b.view(a.dtype).reshape(-1, r)


# see http://stackoverflow.com/questions/16003217/
def comb_indices(n, k):
    """|N-D| version of itertools.combinations.

    Args:
        a (np.ndarray): The array from which to get combinations.
        k (int): The desired length of the combinations.

    Returns:
        np.ndarray: Indices that give the |k|-combinations of |n| elements.

    Example:
        >>> n, k = 3, 2
        >>> data = np.arange(6).reshape(2, 3)
        >>> data[:, comb_indices(n, k)]
        array([[[0, 1],
                [0, 2],
                [1, 2]],
        <BLANKLINE>
               [[3, 4],
                [3, 5],
                [4, 5]]])
    """
    # Count the number of combinations for preallocation
    count = comb(n, k, exact=True)
    # Get numpy iterable from ``itertools.combinations``
    indices = np.fromiter(
        chain.from_iterable(combinations(range(n), k)),
        int,
        count=(count * k))
    # Reshape output into the array of combination indicies
    return indices.reshape(-1, k)


# TODO? implement this with numpy
def powerset(iterable):
    """Return the power set of an iterable (see `itertools recipes
    <http://docs.python.org/2/library/itertools.html#recipes>`_).

    Args:
        iterable (Iterable): The iterable from which to generate the power set.

    Returns:
        generator: An chained generator over the power set.

    Example:
        >>> ps = powerset(np.arange(2))
        >>> print(list(ps))
        [(), (0,), (1,), (0, 1)]
    """
    return chain.from_iterable(combinations(iterable, r)
                               for r in range(len(iterable) + 1))


def uniform_distribution(number_of_nodes):
    """
    Return the uniform distribution for a set of binary nodes, indexed by state
    (so there is one dimension per node, the size of which is the number of
    possible states for that node).

    Args:
        nodes (np.ndarray): A set of indices of binary nodes.

    Returns:
        np.ndarray: The uniform distribution over the set of nodes.
    """
    # The size of the state space for binary nodes is 2^(number of nodes).
    number_of_states = 2 ** number_of_nodes
    # Generate the maximum entropy distribution
    # TODO extend to nonbinary nodes
    return (np.ones(number_of_states) /
            number_of_states).reshape([2] * number_of_nodes)


def marginalize_out(indices, tpm):
    """
    Marginalize out a node from a TPM.

    Args:
        indices (list[int]): The indices of nodes to be marginalized out.
        tpm (np.ndarray): The TPM to marginalize the node out of.

    Returns:
        np.ndarray: A TPM with the same number of dimensions, with the nodes
        marginalized out.
    """
    return tpm.sum(tuple(indices), keepdims=True) / (
        np.array(tpm.shape)[list(indices)].prod())


def marginal_zero(repertoire, node_index):
    """Return the marginal probability that the node is off."""
    index = [slice(None) for i in range(repertoire.ndim)]
    index[node_index] = 0

    return repertoire[index].sum()


def marginal(repertoire, node_index):
    """Get the marginal distribution for a node."""
    index = tuple(i for i in range(repertoire.ndim) if i != node_index)

    return repertoire.sum(index, keepdims=True)


def independent(repertoire):
    """Check whether the repertoire is independent."""
    marginals = [marginal(repertoire, i) for i in range(repertoire.ndim)]

    # TODO: is there a way to do without an explicit iteration?
    joint = marginals[0]
    for m in marginals[1:]:
        joint = joint * m

    # TODO: should we round here?
    # repertoire = repertoire.round(config.PRECISION)
    # joint = joint.round(config.PRECISION)

    return np.array_equal(repertoire, joint)


def purview(repertoire):
    """The purview of the repertoire.

    Args:
        repertoire (np.ndarray): A repertoire

    Returns:
        tuple[int]: The purview that the repertoire was computed over.
    """
    if repertoire is None:
        return None

    return tuple(np.where(np.array(repertoire.shape) == 2)[0])


def purview_size(repertoire):
    """Return the size of the purview of the repertoire.

    Args:
        repertoire (np.ndarray): A repertoire

    Returns:
        int: The size of purview that the repertoire was computed over.
    """
    return len(purview(repertoire))


def repertoire_shape(purview, N):
    """Return the shape a repertoire.

    Args:
        purview (tuple[int]): The purview over which the repertoire is
            computed.
        N (int): The number of elements in the system.

    Returns:
        list[int]: The shape of the repertoire. Purview nodes have two
        dimensions and non-purview nodes are collapsed to a unitary dimension.

    Example:
        >>> purview = (0, 2)
        >>> N = 3
        >>> repertoire_shape(purview, N)
        [2, 1, 2]
    """
    # TODO: extend to non-binary nodes
    return [2 if i in purview else 1 for i in range(N)]


@cache(cache={}, maxmem=None)
def max_entropy_distribution(node_indices, number_of_nodes):
    """Return the maximum entropy distribution over a set of nodes.

    This is different from the network's uniform distribution because nodes
    outside ``node_indices`` are fixed and treated as if they have only 1
    state.

    Args:
        node_indices (tuple[int]): The set of node indices over which to take
            the distribution.
        number_of_nodes (int): The total number of nodes in the network.

    Returns:
        np.ndarray: The maximum entropy distribution over the set of nodes.
    """
    distribution = np.ones(repertoire_shape(node_indices, number_of_nodes))

    return distribution / distribution.size


def bipartition(a):
    """Return a list of bipartitions for a sequence.

    Args:
        a (Iterable): The iterable to partition.

    Returns:
        list[tuple[tuple]]: A list of tuples containing each of the two
        partitions.

    Example:
        >>> bipartition((1,2,3))
        [((), (1, 2, 3)), ((1,), (2, 3)), ((2,), (1, 3)), ((1, 2), (3,))]
    """
    return [(tuple(a[i] for i in part0_idx), tuple(a[j] for j in part1_idx))
            for part0_idx, part1_idx in bipartition_indices(len(a))]


# TODO? [optimization] optimize this to use indices rather than nodes
# TODO? are native lists really slower
def directed_bipartition(a):
    """Return a list of directed bipartitions for a sequence.

    Args:
        a (Iterable): The iterable to partition.

    Returns:
        list[tuple[tuple]]: A list of tuples containing each of the two
        partitions.

    Example:
        >>> directed_bipartition((1, 2, 3))  # doctest: +NORMALIZE_WHITESPACE
        [((), (1, 2, 3)),
         ((1,), (2, 3)),
         ((2,), (1, 3)),
         ((1, 2), (3,)),
         ((3,), (1, 2)),
         ((1, 3), (2,)),
         ((2, 3), (1,)),
         ((1, 2, 3), ())]
    """
    return [(tuple(a[i] for i in part0_idx), tuple(a[j] for j in part1_idx))
            for part0_idx, part1_idx in directed_bipartition_indices(len(a))]


def directed_bipartition_of_one(a):
    """Return a list of directed bipartitions for a sequence where each
    bipartitions includes a set of size 1.

    Args:
        a (Iterable): The iterable to partition.

    Returns:
        list[tuple[tuple]]: A list of tuples containing each of the two
        partitions.

    Example:
        >>> directed_bipartition_of_one((1,2,3))  # doctest: +NORMALIZE_WHITESPACE
        [((1,), (2, 3)),
         ((2,), (1, 3)),
         ((1, 2), (3,)),
         ((3,), (1, 2)),
         ((1, 3), (2,)),
         ((2, 3), (1,))]
    """
    return [partition for partition in directed_bipartition(a)
            if len(partition[0]) == 1 or len(partition[1]) == 1]


@cache(cache={}, maxmem=None)
def directed_bipartition_indices(N):
    """Return indices for directed bipartitions of a sequence.

    Args:
        N (int): The length of the sequence.

    Returns:
        list: A list of tuples containing the indices for each of the two
        partitions.

    Example:
        >>> N = 3
        >>> directed_bipartition_indices(N)  # doctest: +NORMALIZE_WHITESPACE
        [((), (0, 1, 2)),
         ((0,), (1, 2)),
         ((1,), (0, 2)),
         ((0, 1), (2,)),
         ((2,), (0, 1)),
         ((0, 2), (1,)),
         ((1, 2), (0,)),
         ((0, 1, 2), ())]
    """
    indices = bipartition_indices(N)
    return indices + [idx[::-1] for idx in indices[::-1]]


@cache(cache={}, maxmem=None)
def bipartition_indices(N):
    """Return indices for undirected bipartitions of a sequence.

    Args:
        N (int): The length of the sequence.

    Returns:
        list: A list of tuples containing the indices for each of the two
        partitions.

    Example:
        >>> N = 3
        >>> bipartition_indices(N)
        [((), (0, 1, 2)), ((0,), (1, 2)), ((1,), (0, 2)), ((0, 1), (2,))]
    """
    result = []
    if N <= 0:
        return result

    for i in range(2**(N - 1)):
        part = [[], []]
        for n in range(N):
            bit = (i >> n) & 1
            part[bit].append(n)
        result.append((tuple(part[1]), tuple(part[0])))
    return result


@cache(cache={}, maxmem=None)
def directed_tripartition_indices(N):
    """Return indices for directed tripartitions of a sequence.

    Args:
        N (int): The length of the sequence.

    Returns:
        list[tuple]: A list of tuples containing the indices for each
            partition.

    Example:
        >>> N = 1
        >>> directed_tripartition_indices(N)
        [((0,), (), ()), ((), (0,), ()), ((), (), (0,))]
    """

    result = []
    if N <= 0:
        return result

    base = [0, 1, 2]
    for key in product(base, repeat=N):
        part = [[], [], []]
        for i, location in enumerate(key):
            part[location].append(i)

        result.append(tuple(tuple(p) for p in part))

    return result


def directed_tripartition(seq):
    """Generator over all directed tripartitions of a sequence.

    Args:
        seq (Iterable): a sequence.

    Yields:
        tuple[tuple]: A tripartition of ``seq``.

    Example:
        >>> seq = (2, 5)
        >>> list(directed_tripartition(seq))  # doctest: +NORMALIZE_WHITESPACE
        [((2, 5), (), ()),
         ((2,), (5,), ()),
         ((2,), (), (5,)),
         ((5,), (2,), ()),
         ((), (2, 5), ()),
         ((), (2,), (5,)),
         ((5,), (), (2,)),
         ((), (5,), (2,)),
         ((), (), (2, 5))]
    """
    for a, b, c in directed_tripartition_indices(len(seq)):
        yield (tuple(seq[i] for i in a),
               tuple(seq[j] for j in b),
               tuple(seq[k] for k in c))


def load_data(directory, num):
    """Load numpy data from the data directory.

    The files should stored in ``../data/{dir}`` and named
    ``0.npy, 1.npy, ... {num - 1}.npy``.

    Returns:
        list: A list of loaded data, such that ``list[i]`` contains the the
        contents of ``i.npy``.
    """

    root = os.path.abspath(os.path.dirname(__file__))

    def get_path(i):  # pylint: disable=missing-docstring
        return os.path.join(root, 'data', directory, str(i) + '.npy')

    return [np.load(get_path(i)) for i in range(num)]
