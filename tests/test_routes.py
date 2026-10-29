FORM = {"scenario": "bad_deploy", "duration": "40", "fault_at": "8", "mitigate_after": "15", "seed": "7"}


def make_incident(client, **changes):
    response = client.post("/incidents", data={**FORM, **changes}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def test_health_needs_no_login(anon):
    response = anon.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_pages_need_login(anon):
    assert anon.get("/").status_code == 401
    assert anon.post("/incidents", data=FORM).status_code == 401


def test_wrong_password_is_rejected(anon):
    assert anon.get("/", auth=("admin", "wrong")).status_code == 401


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200 and "Run an incident drill" in response.text


def test_run_a_drill_and_view_it(client):
    location = make_incident(client)
    assert location == "/incidents/1"
    page = client.get(location)
    assert page.status_code == 200
    assert "Bad deployment" in page.text and "Time to detect" in page.text and "<svg" in page.text


def test_invalid_form_shows_errors(client):
    response = client.post("/incidents", data={**FORM, "duration": "5"})
    assert response.status_code == 422 and "Enter a number from 20 to 120" in response.text


def test_notes_rebuild_the_draft_and_download_works(client):
    location = make_incident(client)
    client.post(f"{location}/notes", data={"cause": "A config change removed a header.", "actions_taken": "",
                                           "went_well": "", "went_poorly": ""})
    download = client.get(f"{location}/postmortem.md")
    assert download.status_code == 200 and "text/markdown" in download.headers["content-type"]
    assert "A config change removed a header." in download.text


def test_polish_without_a_key_explains_why(client):
    location = make_incident(client)
    page = client.post(f"{location}/polish")
    assert page.status_code == 200 and "ANTHROPIC_API_KEY" in page.text


def test_polish_with_a_fake_ai_call(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.llm.call_anthropic", lambda system, user: "# Polished postmortem")
    location = make_incident(client)
    client.post(f"{location}/polish")
    assert "Polished postmortem" in client.get(f"{location}/postmortem.md").text


def test_cross_site_post_is_blocked(client):
    response = client.post("/incidents", data=FORM, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_delete_a_drill(client):
    location = make_incident(client)
    assert client.post(f"{location}/delete").status_code == 200
    assert client.get(location).status_code == 404
