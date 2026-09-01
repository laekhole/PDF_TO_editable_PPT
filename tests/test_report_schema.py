"""The report must match its published schema — that is what makes it usable."""

import json
import os

import pytest

from conftest import ROOT

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_PATH = os.path.join(ROOT, "docs", "report-schema.json")


@pytest.fixture(scope="module")
def validator():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize(
    "name",
    [
        "shapes",
        "images",
        "text_mixed",
        "table_lattice",
        "clip_gradient",
        "dense_vector",
        "scanned",
        "rotated_pages",
        "mixed_sizes",
        "damaged_page",
    ],
)
def test_report_validates_against_the_schema(conversions, validator, name):
    report = conversions.get(name, verify=True).report
    errors = sorted(validator.iter_errors(report), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        "%s: %s" % (list(e.path), e.message) for e in errors[:5]
    )


def test_a_fallback_without_a_reason_is_rejected_by_the_schema(conversions, validator):
    """The schema is what enforces 'no silent failure'; prove it bites."""
    report = json.loads(json.dumps(conversions.get("clip_gradient", verify=True).report))
    element = next(
        e
        for page in report["pages"]
        for e in page["elements"]
        if e["outcome"] == "raster-fallback"
    )
    del element["fallbackReason"]
    assert list(validator.iter_errors(report)), "a reasonless fallback must not validate"


def test_committed_samples_validate(validator):
    import glob

    paths = glob.glob(os.path.join(ROOT, "samples", "*.report.json"))
    assert paths, "the committed samples are part of the deliverable"
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
        errors = list(validator.iter_errors(report))
        assert not errors, "%s: %s" % (os.path.basename(path), errors[0].message)


def test_absorbed_objects_are_recorded_not_dropped(conversions, validator):
    """A source object that another object took over still has a paper trail."""
    report = conversions.get("table_lattice", verify=True).report
    page = report["pages"][0]
    absorbed = page.get("absorbed", [])
    assert absorbed, "the table adopted rules, fills and cell text"
    table_id = next(e["id"] for e in page["elements"] if e["type"] == "table")
    assert all(e["absorbedBy"] == table_id for e in absorbed)
    assert report["summary"]["absorbedElements"] == len(absorbed)
    assert not list(validator.iter_errors(report))


def test_a_fallback_region_records_what_it_covered(conversions, validator):
    report = conversions.get("clip_gradient", verify=True).report
    page = report["pages"][0]
    live_ids = {e["id"] for e in page["elements"]}
    for entry in page.get("absorbed", []):
        assert entry["absorbedBy"] in live_ids
        assert entry.get("fallbackReason")
