/* ── CLVRS global scripts ─────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function () {

    /* Password-reveal toggle (login page) */
    var btn   = document.getElementById('pw-toggle');
    var field = document.getElementById('id_password');
    var icon  = document.getElementById('pw-icon');

    if (btn && field && icon) {
        btn.addEventListener('click', function () {
            var isHidden = field.type === 'password';
            field.type = isHidden ? 'text' : 'password';
            icon.classList.remove('fa-eye', 'fa-eye-slash');
            icon.classList.add(isHidden ? 'fa-eye-slash' : 'fa-eye');
        });
    }

});
