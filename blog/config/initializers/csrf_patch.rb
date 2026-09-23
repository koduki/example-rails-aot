# frozen_string_literal: true

# Use session-wide CSRF tokens instead of per-form tokens to match Spinel AOT's single-token architecture
Rails.application.config.action_controller.per_form_csrf_tokens = false

# Disable field_with_errors wrapper around input fields to match AOT clean form rendering
ActionView::Base.field_error_proc = ->(html_tag, _instance) { html_tag }
