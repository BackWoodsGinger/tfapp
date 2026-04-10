from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.views import PasswordChangeView, PasswordChangeDoneView
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages, auth
from django.contrib.auth.decorators import login_required

from .session_utils import register_user_session


def _safe_next_redirect_url(request):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not next_url:
        return settings.LOGIN_REDIRECT_URL
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return settings.LOGIN_REDIRECT_URL


def login(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']

        user = auth.authenticate(username=username, password=password)

        if user is not None:
            auth.login(request, user)
            register_user_session(user, request.session.session_key)
            messages.success(request, 'You are now logged in')
            return redirect(_safe_next_redirect_url(request))
        else:
            messages.error(request, 'Invalid credentials')
            next_q = request.POST.get("next") or request.GET.get("next")
            if next_q:
                q = urlencode({"next": next_q})
                return redirect(f"{reverse('login')}?{q}")
            return redirect('login')
    else:
        return render(request, 'accounts/login.html')

def logout(request):
    if request.method == 'POST':
        auth.logout(request)
        messages.success(request, 'You are now logged out')
        return redirect('index')

@login_required
def profile(request):
    return render(request, 'accounts/profile.html')


class PasswordChange(PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('password_change_done')

class PasswordChangeDone(PasswordChangeDoneView):
    template_name = 'accounts/password_change_done.html'

