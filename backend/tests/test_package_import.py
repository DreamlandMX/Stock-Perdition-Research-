def test_package_exposes_version() -> None:
    import stock_prediction

    assert stock_prediction.__version__ == "0.1.0"
