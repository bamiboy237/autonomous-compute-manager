"""Schema and mapping checks that need no database."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
import sqlalchemy as sa

from acm_core import domain
from acm_core.db import models
from acm_core.db.base import Base
from acm_core.db.mapping import MAPPINGS, mapping_for
from acm_core.domain import (
    Campaign,
    ComputeTarget,
    Currency,
    Metric,
    MetricDirection,
    Money,
    Run,
)
from acm_core.domain.common import Entity
from acm_core.ids import CampaignId, ComputeTargetId, ExperimentId, ProviderAccountId, WorkloadId


def _entity_types() -> set[type[Entity]]:
    found: set[type[Entity]] = set()
    for name in domain.__all__:
        obj = getattr(domain, name)
        if isinstance(obj, type) and issubclass(obj, Entity) and obj is not Entity:
            found.add(obj)
    return found


def test_every_domain_entity_has_a_mapping():
    assert set(MAPPINGS) == _entity_types()


@pytest.mark.parametrize("mapping", MAPPINGS.values(), ids=lambda m: m.domain.__name__)
def test_columns_match_domain_fields(mapping):
    table_columns = {c.name for c in mapping.row.__table__.columns}
    assert set(mapping.columns) <= table_columns
    extra = table_columns - set(mapping.columns)
    assert extra <= {"version"}, f"{mapping.row.__tablename__} has unmapped columns {extra}"


def test_tables_registered():
    assert {t.name for t in Base.metadata.tables.values()} == {
        m.row.__tablename__ for m in MAPPINGS.values()
    }


def test_one_active_attempt_per_run_index():
    index = next(
        i
        for i in sa.inspect(models.AttemptRow).local_table.indexes  # type: ignore[attr-defined]
        if i.name.startswith("uq_attempts")
    )
    assert index.unique
    where = str(index.dialect_options["postgresql"]["where"])
    for status in models.ACTIVE_ATTEMPT_STATUSES:
        assert f"'{status.value}'" in where
    assert "'succeeded'" not in where


def test_mutable_aggregates_are_versioned():
    for row in (models.RunRow, models.AttemptRow, models.DecisionRow, models.CampaignRow):
        assert "version" in row.__table__.columns
    for row in (models.LedgerEntryRow, models.ModelCallRow, models.CheckpointRow):
        assert "version" not in row.__table__.columns


def test_json_fields_round_trip_non_finite_floats():
    mapping = mapping_for(Run)
    run = Run(
        campaign_id=CampaignId("cmp_01JB3V6Q1Z8K4M2N5P7R9S0T1W"),
        experiment_id=ExperimentId("exp_01JB3V6Q1Z8K4M2N5P7R9S0T1W"),
        final_metrics={"loss": math.nan, "mrr": 0.5},
    )  # type: ignore[arg-type]
    values = mapping.to_values(run)
    assert values["final_metrics"] == {"loss": "NaN", "mrr": 0.5}
    row = models.RunRow(**values)
    back = mapping.from_row(row)
    assert math.isnan(back.final_metrics["loss"])
    assert back.final_metrics["mrr"] == 0.5


def test_campaign_round_trip_through_row():
    mapping = mapping_for(Campaign)
    campaign = Campaign(
        name="c",
        objective="o",
        metric=Metric(name="mrr", direction=MetricDirection.MAXIMIZE, evaluator_ref="a.b:c"),
        workload_id=WorkloadId.new(),
    )
    row = models.CampaignRow(**mapping.to_values(campaign))
    assert isinstance(row.metric, dict)
    assert mapping.from_row(row) == campaign


def test_compute_target_money_and_interval_round_trip():
    from datetime import timedelta

    mapping = mapping_for(ComputeTarget)
    target = ComputeTarget(
        provider_account_id=ProviderAccountId.new(),
        name="gpu",
        max_walltime=timedelta(hours=2),
        price_per_gpu_hour=Money(currency=Currency.USD, amount=Decimal("1.25")),
    )
    assert mapping.from_row(models.ComputeTargetRow(**mapping.to_values(target))) == target
    assert ComputeTargetId(target.id)
