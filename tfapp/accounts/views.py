from django.shortcuts import render, redirect
from django.contrib.auth.views import PasswordChangeView, PasswordChangeDoneView
from django.urls import reverse_lazy
from django.contrib import messages, auth
from django.contrib.auth.decorators import login_required
from django.urls import reverse_lazy


from django.contrib.auth.models import User

def login(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']

        user = auth.authenticate(username=username, password=password)

        if user is not None:
            auth.login(request, user)
            messages.success(request, 'You are now logged in')
            return redirect('index')
        else:
            messages.error(request, 'Invalid credentials')
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

