from app.facebook_business_create import _build_create_variables


def test_create_variables_match_current_scope_selector_footer_capture():
    payload = _build_create_variables(
        actor_id="123456789",
        business_name="Captured Business",
        user_email="owner@example.com",
        user_first_name="Test",
        user_last_name="Owner",
        profile_display_name="Test Owner",
    )

    input_data = payload["input"]

    assert input_data["actor_id"] == "123456789"
    assert input_data["business_name"] == "Captured Business"
    assert input_data["user_email"] == "owner@example.com"
    assert input_data["user_first_name"] == "Test"
    assert input_data["user_last_name"] == "Owner"
    assert input_data["creation_source"] == (
        "MBS_BUSINESS_CREATION_IN_SCOPE_SELECTOR_FOOTER"
    )
    assert input_data["entry_point"] == (
        "BIZWEB_SCOPE_SELECTOR_FOOTER_CREATION_BUTTON"
    )
    assert isinstance(input_data["client_mutation_id"], str)
    assert len(input_data["client_mutation_id"]) == 16
    assert isinstance(input_data["qpl_join_id"], str)
    assert len(input_data["qpl_join_id"]) == 32
