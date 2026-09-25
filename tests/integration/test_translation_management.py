def _csrf_token(client):
    response = client.get('/api/csrf-token')
    assert response.status_code == 200
    return response.get_json()['csrf_token']


def test_translation_manager_is_admin_only_and_edits_runtime_catalog(admin_client, client_factory):
    analyst = client_factory('analyst')
    assert analyst.get('/translations/manage').status_code == 403
    assert analyst.get('/api/translations/manage/catalog?locale=ar').status_code == 403
    analyst_headers = {'X-CSRFToken': _csrf_token(analyst)}
    assert analyst.put(
        '/api/translations/manage/catalog', json={}, headers=analyst_headers,
    ).status_code == 403

    page = admin_client.get('/translations/manage')
    assert page.status_code == 200
    page_html = page.get_data(as_text=True)
    assert 'data-translation-manager' in page_html
    assert 'data-interface="translation_manager"' in page_html

    token = _csrf_token(admin_client)
    headers = {'X-CSRFToken': token}
    catalog_response = admin_client.get(
        '/api/translations/manage/catalog?locale=ar&q=Translation%20Management&per_page=10'
    )
    assert catalog_response.status_code == 200, catalog_response.get_data(as_text=True)
    entry = next(
        row for row in catalog_response.get_json()['items']
        if row['msgid'] == 'Translation Management'
    )
    baseline = entry['base_translation']
    entry_id = entry['id']

    # Ensure the test always leaves the checked-in PO value effective.
    reset_payload = {
        'locale': 'ar',
        'translations': [{'id': entry_id, 'reset': True}],
    }
    admin_client.put(
        '/api/translations/manage/catalog', json=reset_payload, headers=headers,
    )

    invalid_placeholder = admin_client.put(
        '/api/translations/manage/catalog',
        json={'locale': 'ar', 'translations': [{'id': entry_id, 'translation': '{missing}'}]},
        headers=headers,
    )
    assert invalid_placeholder.status_code == 400

    changed_value = 'SYLTHARAE translation runtime check'
    save_response = admin_client.put(
        '/api/translations/manage/catalog',
        json={'locale': 'ar', 'translations': [{'id': entry_id, 'translation': changed_value}]},
        headers=headers,
    )
    assert save_response.status_code == 200, save_response.get_data(as_text=True)

    with admin_client.session_transaction() as session:
        session['language'] = 'ar'
    rendered = admin_client.get('/translations/manage')
    assert rendered.status_code == 200
    assert changed_value in rendered.get_data(as_text=True)

    client_catalog = admin_client.get('/api/i18n/catalog?locale=ar').get_json()
    assert client_catalog['translations']['Translation Management'] == changed_value

    try:
        reset_response = admin_client.put(
            '/api/translations/manage/catalog', json=reset_payload, headers=headers,
        )
        assert reset_response.status_code == 200, reset_response.get_data(as_text=True)
        restored = admin_client.get('/api/i18n/catalog?locale=ar').get_json()
        assert restored['translations']['Translation Management'] == baseline
    finally:
        # Be defensive if an assertion above fails partway through the test.
        admin_client.put(
            '/api/translations/manage/catalog', json=reset_payload, headers=headers,
        )
