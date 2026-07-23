"""Production-like configuration must not enable external services implicitly."""


def test_production_configuration_is_explicit_and_has_no_provider_credentials(tmp_path):
    from argus.config import load_config

    environment = {
        "ARGUS_AUTOLOAD_DOTENV": "false",
        "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
        "ARGUS_ENV": "production",
        "ARGUS_DATA_ROOT": str(tmp_path / "data"),
        "ARGUS_DB_URL": f"sqlite:///{tmp_path / 'argus.db'}",
        "ARGUS_EGRESS_TYPE": "datacenter",
        "ARGUS_RESIDENTIAL_POLICY": "fallback",
        "ARGUS_EGRESS_NODES": "",
        "ARGUS_RESIDENTIAL_SHARED_SECRET": "",
    }
    for provider in (
        "SEARXNG", "BRAVE", "SERPER", "TAVILY", "EXA", "SEARCHAPI", "YOU",
        "PARALLEL", "LINKUP", "VALYU", "GITHUB", "YAHOO", "WOLFRAM", "JINA",
        "FIRECRAWL",
    ):
        environment[f"ARGUS_{provider}_ENABLED"] = "false"

    config = load_config(environ=environment)

    assert config.env == "production"
    assert config.node.egress_type == "datacenter"
    assert config.residential.policy == "fallback"
    assert config.egress_nodes == []
    assert all(
        not provider.enabled
        for provider in (
            config.searxng, config.brave, config.serper, config.tavily, config.exa,
            config.searchapi, config.you, config.parallel, config.linkup, config.valyu,
            config.github, config.yahoo, config.wolfram, config.jina, config.firecrawl,
        )
    )
