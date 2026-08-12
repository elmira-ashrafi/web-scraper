from django import forms


class ScrapeForm(forms.Form):
    url = forms.URLField(
        label="Product URL",
        widget=forms.URLInput(
            attrs={
                "class": "form-input",
                "placeholder": "https://www.amazon.com/dp/... or https://www.walmart.com/ip/...",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
        help_text="Paste an Amazon or Walmart product page link — we'll fetch the details for you.",
    )

    def clean_url(self):
        url = self.cleaned_data["url"]
        lowered = url.lower()
        if "amazon." not in lowered and "walmart." not in lowered:
            raise forms.ValidationError(
                "Please enter a valid Amazon or Walmart product URL."
            )
        return url
