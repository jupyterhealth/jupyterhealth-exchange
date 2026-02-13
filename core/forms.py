from django import forms
from django.contrib.auth import get_user_model

from core.jhe_settings.service import get_setting

User = get_user_model()


class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(label="Password")

    class Meta:
        model = User
        fields = ["email", "password"]

    def clean(self, *args, **kwargs):
        email = self.cleaned_data.get("email")
        password = self.cleaned_data.get("password")
        email_check = User.objects.filter(email=email)
        registration_invite_code = self.data.get("joincode")
        if email_check.exists():
            raise forms.ValidationError("This Email already exists")
        if len(password) < 5:
            raise forms.ValidationError("Your Password should have more than 5 characters")
        if registration_invite_code != get_setting("site.registration_invite_code"):
            raise forms.ValidationError("Your Invite Code is invalid.")
        return super(UserRegistrationForm, self).clean(*args, **kwargs)
