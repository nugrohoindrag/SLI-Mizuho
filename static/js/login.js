function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
}

document.addEventListener("DOMContentLoaded", function () {
    
    const loginForm = document.getElementById("login-form");
    const otpForm = document.getElementById("otp-form");
    const spinner = document.getElementById("loading-spinner");
    const passwordInput = document.getElementById("user-password");
    const passwordValidation = document.getElementById("password-validation");
    const toggleIcon = document.getElementById("togglePasswordIcon");
    const otpTimerEl = document.getElementById("otp-timer");
    const resendBtn = document.getElementById("resend-otp");
    
    /* =======================================================
    PASSWORD UI
    ======================================================= */
    
    if (passwordValidation) passwordValidation.hidden = true;
    
    if (passwordInput) {
        
        passwordInput.addEventListener("focus", () => {
            if (passwordValidation) passwordValidation.hidden = false;
        });
        
        passwordInput.addEventListener("input", function () {
            const v = passwordInput.value;
            
            updateValidation("pw-len", v.length >= 8);
            updateValidation("pw-upper", /[A-Z]/.test(v));
            updateValidation("pw-lower", /[a-z]/.test(v));
            updateValidation("pw-number", /\d/.test(v));
            updateValidation("pw-symbol", /[!@#$%^&*(),.?":{}|<>]/.test(v));
        });
    }
    
    if (toggleIcon && passwordInput) {
        toggleIcon.addEventListener("mousedown", () => {
            passwordInput.type = "text";
            toggleIcon.classList.replace("fa-eye-slash", "fa-eye");
        });
        
        ["mouseup","mouseleave","touchend"].forEach(evt =>
            toggleIcon.addEventListener(evt, () => {
                passwordInput.type = "password";
                toggleIcon.classList.replace("fa-eye","fa-eye-slash");
            })
        );
    }
    
    function updateValidation(id, isValid) {
        const el = document.getElementById(id);
        if (!el) return;
        
        el.classList.remove("text-success", "text-danger");
        el.classList.add(isValid ? "text-success" : "text-danger");
        
        const cleanText = el.textContent.replace("✅ ", "").replace("❌ ", "");
        el.textContent = (isValid ? "✅ " : "❌ ") + cleanText;
    }
    
    /* =======================================================
    OTP COUNTDOWN
    ======================================================= */
    
    let otpDuration = 900; // 15 menit
    let otpInterval = null;
    
    function startOtpTimer() {
        
        clearInterval(otpInterval);
        otpDuration = 900;
        
        resendBtn.disabled = true;
        
        otpInterval = setInterval(() => {
            
            const minutes = Math.floor(otpDuration / 60);
            const seconds = otpDuration % 60;
            
            otpTimerEl.textContent =
            "Berlaku " +
            String(minutes).padStart(2, "0") +
            ":" +
            String(seconds).padStart(2, "0");
            
            otpDuration--;
            
            if (otpDuration < 0) {
                
                clearInterval(otpInterval);
                
                otpTimerEl.textContent = "OTP Sudah Expired";
                otpTimerEl.classList.remove("text-danger");
                otpTimerEl.classList.add("text-dark");
                
                resendBtn.disabled = false;
                
                Swal.fire({
                    icon: "warning",
                    title: "OTP Expired",
                    text: "Silakan kirim ulang OTP."
                });
            }
            
        }, 1000);
    }
    
    /* =======================================================
    LOGIN SUBMIT
    ======================================================= */
    
    if (loginForm) {
        
        loginForm.addEventListener("submit", async function (e) {
            e.preventDefault();
            
            if (spinner) spinner.style.display = "block";
            
            const identifierInput = document.getElementById("user-identifier");
            const identifier = identifierInput ? identifierInput.value.trim() : "";
            const password = passwordInput ? passwordInput.value.trim() : "";
            
            if (!identifier || !password) {
                Swal.fire({
                    icon: "warning",
                    title: "Lengkapi Data",
                    text: "Email/username dan password wajib diisi."
                });
                
                if (spinner) spinner.style.display = "none";
                return;
            }
            
            try {
                
                const res = await fetch("/api/login/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-CSRFToken": getCSRFToken(),
                    },
                    body: JSON.stringify({ identifier, password }),
                    credentials: "include",
                    cache: "no-store"
                });
                
                const isJSON = (res.headers.get("content-type") || "").includes("application/json");
                const data = isJSON ? await res.json() : {};
                
                if (!res.ok) {
                    
                    const msg = data.detail || data.error || "Login Gagal.";
                    const blocked = msg.toLowerCase().includes("diblokir");
                    
                    Swal.fire({
                        icon: blocked ? "error" : "warning",
                        title: blocked ? "Akun Diblokir" : "Login Gagal",
                        text: blocked
                        ? "Terdeteksi 3 kali percobaan gagal. Akun diblokir. Hubungi admin."
                        : msg
                    });
                    
                    return;
                }
                
                /* ===== Butuh OTP ===== */
                if (data.need_otp && otpForm) {
                    
                    loginForm.style.display = "none";
                    otpForm.style.display = "block";
                    
                    startOtpTimer();
                    
                    Swal.fire({
                        icon: "info",
                        title: "OTP Dikirim",
                        text: "Silakan cek email Anda untuk kode OTP."
                    });
                    
                    const firstOtp = document.querySelector(".otp-input");
                    if (firstOtp) firstOtp.focus();
                    
                    return;
                }
                
                /* ===== Tidak Butuh OTP ===== */
                window.location.replace(data.redirect || "/dashboard/");
                
            } catch (err) {
                
                console.error("Login error:", err);
                
                Swal.fire({
                    icon: "error",
                    title: "Login Gagal",
                    text: String(err)
                });
                
            } finally {
                if (spinner) spinner.style.display = "none";
            }
        });
    }
    
    /* =======================================================
    OTP SECTION
    ======================================================= */
    
    if (otpForm) {
        
        const otpInputs = document.querySelectorAll(".otp-input");
        
        otpInputs.forEach((input, index) => {
            
            input.addEventListener("input", () => {
                input.value = input.value.replace(/\D/g, '');
                if (input.value && index < otpInputs.length - 1) {
                    otpInputs[index + 1].focus();
                }
            });
            
            input.addEventListener("keydown", (e) => {
                if (e.key === "Backspace" && !input.value && index > 0) {
                    otpInputs[index - 1].focus();
                }
            });
            
            input.addEventListener("paste", (e) => {
                e.preventDefault();
                const pasted = e.clipboardData.getData("text").replace(/\D/g, '');
                
                if (pasted.length === otpInputs.length) {
                    otpInputs.forEach((box, i) => {
                        box.value = pasted[i] || "";
                    });
                }
            });
        });
        
        otpForm.addEventListener("submit", async function (e) {
            e.preventDefault();
            
            if (spinner) spinner.style.display = "block";
            
            let otp = "";
            otpInputs.forEach(input => otp += input.value);
            
            if (otp.length !== otpInputs.length) {
                Swal.fire({
                    icon: "warning",
                    title: "OTP Tidak Lengkap",
                    text: "Silakan isi 6 digit OTP."
                });
                
                if (spinner) spinner.style.display = "none";
                return;
            }
            
            try {
                
                const res = await fetch("/verify-otp/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCSRFToken(),
                    },
                    body: JSON.stringify({ otp }),
                    credentials: "include",
                    cache: "no-store"
                });
                
                const isJSON = (res.headers.get("content-type") || "").includes("application/json");
                const data = isJSON ? await res.json() : {};
                
                if (!res.ok) {
                    Swal.fire({
                        icon: "warning",
                        title: "OTP Salah",
                        text: data.detail || "OTP tidak valid."
                    });
                    return;
                }
                
                window.location.replace(data.redirect || "/dashboard/");
                
            } catch (err) {
                
                Swal.fire({
                    icon: "error",
                    title: "Error",
                    text: String(err)
                });
                
            } finally {
                if (spinner) spinner.style.display = "none";
            }
        });
        
        /* ===== RESEND OTP ===== */
        if (resendBtn) {
            
            resendBtn.addEventListener("click", async function () {
                
                resendBtn.disabled = true;
                
                try {
                    
                    const res = await fetch("/resend-otp/", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": getCSRFToken(),
                        },
                        credentials: "include"
                    });
                    
                    const data = await res.json();
                    
                    if (!res.ok) {
                        throw new Error(data.detail || "Gagal kirim OTP");
                    }
                    
                    clearInterval(otpInterval);
                    startOtpTimer();
                    
                    Swal.fire({
                        icon: "success",
                        title: "OTP Baru Dikirim",
                        text: "Silakan cek email Anda."
                    });
                    
                } catch (err) {
                    
                    resendBtn.disabled = false;
                    
                    Swal.fire({
                        icon: "error",
                        title: "Gagal",
                        text: err.message
                    });
                }
                
            });
            
        }
    }
    
});