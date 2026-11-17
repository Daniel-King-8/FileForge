/* 登录 / 注册表单 */
(function () {
  const errorBox = document.getElementById('form-error');

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function hideError() {
    if (errorBox) errorBox.hidden = true;
  }

  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      hideError();
      const button = loginForm.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = '登录中…';
      try {
        const data = await FF.api('/api/login', {
          method: 'POST',
          body: JSON.stringify({
            username: loginForm.username.value.trim(),
            password: loginForm.password.value
          })
        });
        const next = loginForm.dataset.next || '';
        const target = next && next.startsWith('/') ? next : '/';
        location.href = data.user.role === 'admin' && !next ? '/admin' : target;
      } catch (error) {
        showError(error.message);
        button.disabled = false;
        button.textContent = '登录';
      }
    });
  }

  const registerForm = document.getElementById('register-form');
  if (registerForm) {
    registerForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      hideError();
      const password = registerForm.password.value;
      if (password !== registerForm.confirm.value) {
        showError('两次输入的密码不一致');
        return;
      }
      const button = registerForm.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = '注册中…';
      try {
        await FF.api('/api/register', {
          method: 'POST',
          body: JSON.stringify({
            username: registerForm.username.value.trim(),
            email: registerForm.email.value.trim(),
            password: password
          })
        });
        location.href = '/';
      } catch (error) {
        showError(error.message);
        button.disabled = false;
        button.textContent = '注册并登录';
      }
    });
  }
})();
