from oauth2_provider.oauth2_validators import OAuth2Validator


class JheOAuth2Validator(OAuth2Validator):
    """Extend DOT's default validator so /o/userinfo/ returns the email claim
    when the 'email' scope is granted.

    DOT's built-in ``oidc_claim_scope`` already maps ``"email" -> "email"``
    so we only need to supply the actual claim value here.
    """

    def get_additional_claims(self, request):
        return {
            "email": request.user.email,
        }
