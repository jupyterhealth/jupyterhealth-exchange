from django import forms
from django.contrib.auth import get_user_model

from core.models import InviteCode

User = get_user_model()


class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(label="Password")

    class Meta:
        model = User
        fields = ["email", "password"]

    def clean(self):
        cleaned = super().clean()
        email = self.cleaned_data.get("email")
        password = self.cleaned_data.get("password")
        # case-insensitive
        requested_code = self.data.get("joincode", "").lower()
        cleaned["joincode"] = requested_code
        # lookup invite code in db
        try:
            InviteCode.objects.get(code=requested_code, enabled=True)
        except InviteCode.DoesNotExist:
            raise forms.ValidationError("Your Invite Code is invalid")

        email_check = User.objects.filter(email=email)
        if email_check.exists():
            raise forms.ValidationError("This Email already exists")
        if len(password) < 5:
            raise forms.ValidationError("Your Password should have more than 5 characters")
        return cleaned
