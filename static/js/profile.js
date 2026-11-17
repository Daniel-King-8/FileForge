/* 个人中心：修改密码 */
(function () {
  const form = document.getElementById('password-form');
  if (!form) return;
  const errorBox = document.getElementById('form-error');

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    errorBox.hidden = true;
    if (form.new_password.value !== form.confirm.value) {
      errorBox.textContent = '两次输入的新密码不一致';
      errorBox.hidden = false;
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await FF.api('/api/profile/password', {
        method: 'POST',
        body: JSON.stringify({
          old_password: form.old_password.value,
          new_password: form.new_password.value
        })
      });
      FF.toast('密码已修改，请重新登录', 'ok');
      setTimeout(function () { location.href = '/login'; }, 900);
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      button.disabled = false;
    }
  });
})();
