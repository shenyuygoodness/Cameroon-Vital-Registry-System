document.addEventListener('DOMContentLoaded', function () {
  var input = document.getElementById('id_code');
  if (input) {
    input.focus();
    input.addEventListener('input', function () {
      this.value = this.value.replace(/\D/g, '').slice(0, 9);
    });
  }
});
