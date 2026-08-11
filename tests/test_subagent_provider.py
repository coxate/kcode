from kcode.config import ProviderConfig
from kcode.subagents.provider import ProviderPool


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, config):
        self.config = config


def config(name: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        model="fake",
        base_url="https://example.test",
        api_key="secret",
    )


def test_provider_pool_inherits_routes_and_caches(monkeypatch) -> None:
    active = Provider(config("main"))
    created: list[Provider] = []

    def create(value):
        provider = Provider(value)
        created.append(provider)
        return provider, ("provider warning",)

    monkeypatch.setattr("kcode.subagents.provider.create_provider", create)
    pool = ProviderPool(active, {"main": active.config, "small": config("small")})
    assert pool.names == {"main", "small"}
    assert pool.get("inherit", active) is active
    first = pool.get("small", active)
    assert pool.get("small", active) is first
    assert created == [first]
    assert pool.warnings == ["provider warning"]


def test_provider_pool_rejects_unknown_provider() -> None:
    active = Provider(config("main"))
    pool = ProviderPool(active, {"main": active.config})
    try:
        pool.get("missing", active)
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown providers must fail")
