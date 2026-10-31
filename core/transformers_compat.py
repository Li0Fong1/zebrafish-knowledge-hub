"""兼容 sentence-transformers 5.x 与 transformers 4.37 的缺失 Config 类。"""


def patch_transformers_compat():
    import transformers
    from transformers import PretrainedConfig

    stubs = [
        "UdopConfig",
        "SwitchTransformersConfig",
        "PegasusXConfig",
        "UMT5Config",
    ]
    for name in stubs:
        if hasattr(transformers, name):
            continue

        mt = name.replace("Config", "").lower()

        def _make_config(config_name, model_type_name):
            class _StubConfig(PretrainedConfig):
                pass
            _StubConfig.__name__ = config_name
            _StubConfig.model_type = model_type_name
            return _StubConfig

        setattr(transformers, name, _make_config(name, mt))
