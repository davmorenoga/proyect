# new_big_phi/__init__.py

from dataclasses import dataclass
from enum import Enum, auto, unique
from textwrap import indent
from typing import Iterable, Optional, Tuple, Union

from .. import compute, conf, connectivity, utils
from ..compute.network import reachable_subsystems
from ..compute.parallel import MapReduce
from ..conf import config, fallback
from ..data_structures import PyPhiFloat
from ..direction import Direction
from ..labels import NodeLabels
from ..models import cmp, fmt
from ..models.cuts import Cut, GeneralKCut, SystemPartition
from ..models.mechanism import RepertoireIrreducibilityAnalysis
from ..models.subsystem import CauseEffectStructure, SystemStateSpecification
from ..partition import system_partitions
from ..relations import ConcreteRelations, Relations
from ..relations import relations as compute_relations
from ..subsystem import Subsystem
from ..warnings import warn_about_tie_serialization

##############################################################################
# Information
##############################################################################


# TODO(4.0) refactor
def system_intrinsic_information(
    subsystem: Subsystem,
    repertoire_distance: Optional[str] = None,
) -> SystemStateSpecification:
    """Return the cause/effect states specified by the system.

    NOTE: Uses ``config.REPERTOIRE_DISTANCE_INFORMATION``.
    NOTE: State ties are arbitrarily broken (for now).
    """
    repertoire_distance = fallback(
        repertoire_distance, config.REPERTOIRE_DISTANCE_INFORMATION
    )
    # TODO(ties) deal with ties here
    cause, effect = [
        subsystem.intrinsic_information(
            direction,
            mechanism=subsystem.node_indices,
            purview=subsystem.node_indices,
            repertoire_distance=repertoire_distance,
        )
        for direction in Direction.both()
    ]
    return SystemStateSpecification(cause=cause, effect=effect)


##############################################################################
# Integration
##############################################################################


@dataclass
class SystemIrreducibilityAnalysis(cmp.Orderable):
    phi: float
    partition: Union[Cut, SystemPartition]
    normalized_phi: float = 0
    cause: Optional[RepertoireIrreducibilityAnalysis] = None
    effect: Optional[RepertoireIrreducibilityAnalysis] = None
    system_state: Optional[SystemStateSpecification] = None
    current_state: Optional[Tuple[int]] = None
    node_indices: Optional[Tuple[int]] = None
    node_labels: Optional[NodeLabels] = None
    reasons: Optional[list] = None

    def __post_init__(self):
        self.phi = PyPhiFloat(self.phi)
        self.normalized_phi = PyPhiFloat(self.normalized_phi)

    _sia_attributes = [
        "phi",
        "normalized_phi",
        "partition",
        "repertoire",
        "partitioned_repertoire",
        "system_state",
    ]

    def order_by(self):
        return self.phi

    @property
    def ties(self):
        try:
            return self._ties
        except AttributeError:
            self._ties = [self]
            return self._ties

    def set_ties(self, ties):
        self._ties = ties

    def __eq__(self, other):
        return cmp.general_eq(self, other, self._sia_attributes)

    def __bool__(self):
        """Whether |big_phi > 0|."""
        return utils.is_positive(self.phi)

    def __hash__(self):
        return hash(
            (
                self.phi,
                self.partition,
            )
        )

    def _repr_columns(self):
        columns = (
            [
                (
                    "Subsystem",
                    ",".join(self.node_labels.coerce_to_labels(self.node_indices)),
                ),
                ("Current state", ",".join(map(str, self.current_state))),
                (f"           {fmt.SMALL_PHI}_s", self.phi),
                (f"Normalized {fmt.SMALL_PHI}_s", self.normalized_phi),
            ]
            + self.system_state._repr_columns()
            + [("#(tied MIPs)", len(self.ties) - 1), ("Partition", "")]
        )
        if self.reasons:
            columns.append(("Reasons", ", ".join(self.reasons)))
        return columns

    def __repr__(self):
        body = "\n".join(fmt.align_columns(self._repr_columns()))
        body = fmt.header(self.__class__.__name__, body, under_char=fmt.HEADER_BAR_2)
        body = fmt.center(body)
        column_extent = body.split("\n")[2].index(":")
        if self.partition:
            body += "\n" + indent(str(self.partition), " " * (column_extent + 2))
        return fmt.box(body)

    def to_json(self):
        warn_about_tie_serialization(self.__class__.__name__, serialize=True)
        dct = self.__dict__.copy()
        # TODO(ties) implement serialization of ties
        # Remove ties because of circular references
        del dct["_ties"]
        return dct


class NullSystemIrreducibilityAnalysis(SystemIrreducibilityAnalysis):
    def __init__(self, **kwargs):
        super().__init__(
            phi=0,
            partition=None,
            cause=None,
            effect=None,
            **kwargs,
        )

    def _repr_columns(self):
        columns = [
            (
                "Subsystem",
                ",".join(self.node_labels.coerce_to_labels(self.node_indices)),
            ),
            (f"           {fmt.BIG_PHI}", self.phi),
        ] + self.system_state._repr_columns()
        if self.reasons:
            columns.append(("Reasons", ", ".join([r.name for r in self.reasons])))
        return columns


def normalization_factor(partition: Union[Cut, GeneralKCut]) -> float:
    if hasattr(partition, "normalization_factor"):
        return partition.normalization_factor()
    return 1 / (len(partition.from_nodes) * len(partition.to_nodes))


def integration_value(
    direction: Direction,
    subsystem: Subsystem,
    cut_subsystem: Subsystem,
    system_state: SystemStateSpecification,
    repertoire_distance: str = None,
) -> float:
    repertoire_distance = fallback(repertoire_distance, config.REPERTOIRE_DISTANCE)
    # TODO(4.0) deal with proliferation of special cases for GID
    if repertoire_distance == "GENERALIZED_INTRINSIC_DIFFERENCE":
        partitioned_repertoire = cut_subsystem.forward_repertoire(
            direction, subsystem.node_indices, subsystem.node_indices
        ).squeeze()[system_state[direction].state]
    else:
        partitioned_repertoire = cut_subsystem.repertoire(
            direction, subsystem.node_indices, subsystem.node_indices
        )
    return subsystem.evaluate_partition(
        direction,
        subsystem.node_indices,
        subsystem.node_indices,
        cut_subsystem.cut,
        partitioned_repertoire=partitioned_repertoire,
        repertoire_distance=repertoire_distance,
        state=system_state[direction],
    )


def evaluate_partition(
    partition: Cut,
    subsystem: Subsystem,
    system_state: SystemStateSpecification,
    repertoire_distance: str = None,
    directions: Optional[Iterable[Direction]] = None,
) -> SystemIrreducibilityAnalysis:
    directions = fallback(directions, Direction.both())
    cut_subsystem = subsystem.apply_cut(partition)
    integration = {
        direction: integration_value(
            direction,
            subsystem,
            cut_subsystem,
            system_state,
            repertoire_distance=repertoire_distance,
        )
        for direction in Direction.both()
    }
    phi = min(integration[direction].phi for direction in directions)
    norm = normalization_factor(partition)
    normalized_phi = phi * norm

    return SystemIrreducibilityAnalysis(
        phi=phi,
        normalized_phi=normalized_phi,
        cause=integration[Direction.CAUSE],
        effect=integration[Direction.EFFECT],
        partition=partition,
        system_state=system_state,
        current_state=subsystem.proper_state,
        node_indices=subsystem.node_indices,
        node_labels=subsystem.node_labels,
    )


@unique
class ShortCircuitConditions(Enum):
    NO_VALID_PARTITIONS = auto()
    NO_CAUSE = auto()
    NO_EFFECT = auto()


def _has_no_cause_or_effect(system_state):
    reasons = []
    for direction, reason in zip(
        Direction.both(),
        [ShortCircuitConditions.NO_CAUSE, ShortCircuitConditions.NO_EFFECT],
    ):
        if system_state[direction].intrinsic_information <= 0:
            reasons.append(reason)
    return reasons


def sia_minimization_key(sia):
    """Return a sorting key that minimizes the normalized phi value.

    Ties are broken by maximizing the phi value."""
    return (sia.normalized_phi, -sia.phi)


def sia(
    subsystem: Subsystem,
    repertoire_distance: str = None,
    directions: Optional[Iterable[Direction]] = None,
    partition_scheme: str = None,
    partitions: Optional[Iterable] = None,
    **kwargs,
) -> SystemIrreducibilityAnalysis:
    """Find the minimum information partition of a system."""
    partition_scheme = fallback(partition_scheme, config.SYSTEM_PARTITION_TYPE)

    # TODO(4.0): trivial reducibility

    filter_func = None
    if partitions == "GENERAL":

        def is_disconnecting_partition(partition):
            # Special case for length 1 subsystems so complete partition is included
            return (
                not connectivity.is_strong(subsystem.apply_cut(partition).proper_cm)
            ) or len(subsystem) == 1

        filter_func = is_disconnecting_partition

    partitions = fallback(
        partitions,
        system_partitions(
            subsystem.node_indices,
            node_labels=subsystem.node_labels,
            partition_scheme=partition_scheme,
            filter_func=filter_func,
        ),
    )

    system_state = system_intrinsic_information(subsystem)

    def _null_sia(**kwargs):
        return NullSystemIrreducibilityAnalysis(
            system_state=system_state,
            node_indices=subsystem.node_indices,
            node_labels=subsystem.node_labels,
            **kwargs,
        )

    shortcircuit_reasons = _has_no_cause_or_effect(system_state)
    if shortcircuit_reasons:
        return _null_sia(reasons=shortcircuit_reasons)

    default_sia = _null_sia(reasons=[ShortCircuitConditions.NO_VALID_PARTITIONS])

    parallel_kwargs = conf.parallel_kwargs(config.PARALLEL_CUT_EVALUATION, **kwargs)
    sias = MapReduce(
        evaluate_partition,
        partitions,
        map_kwargs=dict(
            subsystem=subsystem,
            system_state=system_state,
            repertoire_distance=repertoire_distance,
            directions=directions,
        ),
        shortcircuit_func=utils.is_falsy,
        desc="Evaluating partitions",
        **parallel_kwargs,
    ).run()

    # Find MIP in one pass, keeping track of ties
    # TODO(ties) refactor into resolve_ties module
    mip_sia = default_sia
    mip_key = (float("inf"), float("-inf"))
    ties = [default_sia]
    for candidate_mip_sia in sias:
        candidate_key = sia_minimization_key(candidate_mip_sia)
        if candidate_key < mip_key:
            mip_sia = candidate_mip_sia
            mip_key = candidate_key
            ties = [mip_sia]
        elif candidate_key == mip_key:
            ties.append(candidate_mip_sia)
    for tied_mip in ties:
        tied_mip.set_ties(ties)
    return mip_sia


_sia = sia


##############################################################################
# Composition
##############################################################################


@dataclass
class PhiStructure(cmp.Orderable):
    sia: SystemIrreducibilityAnalysis
    distinctions: CauseEffectStructure
    relations: Relations

    _SIA_INHERITED_ATTRIBUTES = ["phi", "partition", "system_state"]

    def __getattr__(self, attr):
        if attr in self._SIA_INHERITED_ATTRIBUTES:
            return getattr(self.sia, attr)
        return super().__getattribute__(attr)

    def order_by(self):
        return self.phi

    def __hash__(self):
        return hash((self.distinctions, self.relations))

    def __bool__(self):
        return bool(self.sia)

    def __eq__(self, other):
        return cmp.general_eq(
            self,
            other,
            [
                "sia",
                "distinctions",
                "relations",
            ],
        )

    def _repr_columns(self):
        return [
            ("Φ", self.big_phi),
            ("#(distinctions)", len(self.distinctions)),
            ("Σ φ_d", self.sum_phi_distinctions),
            ("#(relations)", len(self.relations)),
            ("Σ φ_r", self.sum_phi_relations),
        ]

    def __repr__(self):
        body = "\n".join(fmt.align_columns(self._repr_columns()))
        body = fmt.header(self.__class__.__name__, body, under_char=fmt.HEADER_BAR_1)
        body += "\n" + str(self.sia)
        return fmt.box(fmt.center(body))

    @property
    def sum_phi_relations(self):
        return self.relations.sum_phi()

    @property
    def sum_phi_distinctions(self):
        try:
            return self._sum_phi_distinctions
        except AttributeError:
            # TODO delegate sum to distinctions
            self._sum_phi_distinctions = sum(self.distinctions.phis)
            return self._sum_phi_distinctions

    @property
    def big_phi(self):
        try:
            return self._big_phi
        except AttributeError:
            self._big_phi = self.sum_phi_distinctions + self.sum_phi_relations
            return self._big_phi


class NullPhiStructure(PhiStructure):
    def __init__(self, **kwargs):
        super().__init__(
            sia=NullSystemIrreducibilityAnalysis(),
            distinctions=CauseEffectStructure([]),
            relations=ConcreteRelations([]),
            **kwargs,
        )


def phi_structure(
    subsystem: Subsystem,
    parallel: bool = True,
    sia: SystemIrreducibilityAnalysis = None,
    distinctions: CauseEffectStructure = None,
    relations: Relations = None,
    sia_kwargs: dict = None,
    ces_kwargs: dict = None,
    relations_kwargs: dict = None,
) -> PhiStructure:
    """Analyze the irreducible cause-effect structure of a system."""
    defaults = dict(parallel=parallel)
    sia_kwargs = {**defaults, **(sia_kwargs or {})}
    ces_kwargs = {**defaults, **(ces_kwargs or {})}
    relations_kwargs = {**defaults, **(relations_kwargs or {})}

    # Analyze irreducibility if not provided
    if sia is None:
        sia = _sia(subsystem, **sia_kwargs)

    # Compute distinctions if not provided
    if distinctions is None:
        distinctions = compute.ces(subsystem, **ces_kwargs)
    # Filter out incongruent distinctions
    distinctions = distinctions.resolve_congruence(sia.system_state)

    # Compute relations if not provided
    if relations is None:
        relations = compute_relations(distinctions, **relations_kwargs)

    return PhiStructure(
        sia=sia,
        distinctions=distinctions,
        relations=relations,
    )


def all_complexes(network, state, **kwargs):
    """Yield SIAs for all subsystems of the network."""
    # TODO(4.0) parallelize
    for subsystem in reachable_subsystems(network, network.node_indices, state):
        yield sia(subsystem, **kwargs)


def irreducible_complexes(network, state, **kwargs):
    """Yield SIAs for irreducible subsystems of the network."""
    # TODO(4.0) parallelize
    yield from filter(None, all_complexes(network, state, **kwargs))


def maximal_complex(network, state, **kwargs):
    # TODO(4.0) parallelize
    return max(
        irreducible_complexes(network, state, **kwargs), default=NullPhiStructure()
    )
