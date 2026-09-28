def authorized_client(app):
    """Existing route tests exercise authenticated desktop actions; security tests use raw clients."""
    client = app.test_client()
    client.environ_base["HTTP_X_AV_TOKEN"] = app.config["ACTION_TOKEN"]
    return client
