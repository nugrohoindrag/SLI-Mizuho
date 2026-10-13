document.addEventListener("DOMContentLoaded", function () {
    const loginSuccess = localStorage.getItem("login_success");
    if (loginSuccess === "true") {
        Swal.fire({
            title: "Berhasil!",
            text: "Login sukses. Selamat datang!",
            icon: "success",
            confirmButtonText: "OK"
        });
        
        localStorage.removeItem("login_success"); // ✅ agar hanya muncul sekali
    }
});