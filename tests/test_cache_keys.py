from cache.keys import build_cache_key, build_cache_key_with_json, build_grouping_spec


def _base_kwargs(**overrides):
    kwargs = dict(
        dataset_id="ds1",
        sensor="UHF",
        metric_id="rms",
        metric_version=1,
        normalization_version="v1_divide_by_vrange",
        metric_params={"bins": 64},
        grouping=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_same_inputs_produce_same_key():
    k1 = build_cache_key(**_base_kwargs())
    k2 = build_cache_key(**_base_kwargs())
    assert k1 == k2


def test_param_dict_order_does_not_affect_key():
    k1 = build_cache_key(**_base_kwargs(metric_params={"a": 1, "b": 2}))
    k2 = build_cache_key(**_base_kwargs(metric_params={"b": 2, "a": 1}))
    assert k1 == k2


def test_different_metric_params_produce_different_key():
    k1 = build_cache_key(**_base_kwargs(metric_params={"bins": 64}))
    k2 = build_cache_key(**_base_kwargs(metric_params={"bins": 32}))
    assert k1 != k2


def test_different_metric_version_produces_different_key():
    k1 = build_cache_key(**_base_kwargs(metric_version=1))
    k2 = build_cache_key(**_base_kwargs(metric_version=2))
    assert k1 != k2


def test_different_normalization_version_produces_different_key():
    k1 = build_cache_key(**_base_kwargs(normalization_version="v1_divide_by_vrange"))
    k2 = build_cache_key(**_base_kwargs(normalization_version="v2_something_else"))
    assert k1 != k2


def test_different_dataset_id_produces_different_key():
    k1 = build_cache_key(**_base_kwargs(dataset_id="ds1"))
    k2 = build_cache_key(**_base_kwargs(dataset_id="ds2"))
    assert k1 != k2


def test_different_sensor_produces_different_key():
    k1 = build_cache_key(**_base_kwargs(sensor="UHF"))
    k2 = build_cache_key(**_base_kwargs(sensor="AE"))
    assert k1 != k2


def test_puntual_vs_grouped_same_metric_produce_different_key():
    k_puntual = build_cache_key(**_base_kwargs(grouping=None))
    k_grouped = build_cache_key(**_base_kwargs(grouping=build_grouping_spec("by_time", 60.0, reducer="median")))
    assert k_puntual != k_grouped


def test_different_grouping_value_produces_different_key():
    g1 = build_grouping_spec("by_time", 60.0, reducer="median")
    g2 = build_grouping_spec("by_time", 30.0, reducer="median")
    k1 = build_cache_key(**_base_kwargs(grouping=g1))
    k2 = build_cache_key(**_base_kwargs(grouping=g2))
    assert k1 != k2


def test_different_reducer_produces_different_key():
    g1 = build_grouping_spec("by_time", 60.0, reducer="median")
    g2 = build_grouping_spec("by_time", 60.0, reducer="mean")
    k1 = build_cache_key(**_base_kwargs(grouping=g1))
    k2 = build_cache_key(**_base_kwargs(grouping=g2))
    assert k1 != k2


def test_percentile_q_only_present_for_percentile_reducer():
    spec_median = build_grouping_spec("by_time", 60.0, reducer="median", percentile_q=75.0)
    spec_percentile = build_grouping_spec("by_time", 60.0, reducer="percentile", percentile_q=75.0)
    assert "percentile_q" not in spec_median
    assert spec_percentile["percentile_q"] == 75.0


def test_intrinsic_group_metric_grouping_spec_has_no_reducer():
    spec = build_grouping_spec("by_time", 60.0)  # sin reducer, para tasa_pulsos/energia/rafagas
    assert "reducer" not in spec
    assert "percentile_q" not in spec


def test_build_cache_key_with_json_digest_matches_build_cache_key():
    key1 = build_cache_key(**_base_kwargs())
    key2, canonical_json = build_cache_key_with_json(**_base_kwargs())
    assert key1 == key2
    assert '"metric_id":"rms"' in canonical_json


def test_key_is_a_valid_sha256_hex_digest():
    key = build_cache_key(**_base_kwargs())
    assert len(key) == 64
    int(key, 16)  # no debe lanzar -- es hex válido
