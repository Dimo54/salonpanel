document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            var message = form.getAttribute("data-confirm") || "Da li ste sigurni?";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    var serviceSelect = document.getElementById("serviceSelect");
    var priceInput = document.getElementById("priceInput");
    if (serviceSelect && priceInput) {
        serviceSelect.addEventListener("change", function () {
            var option = serviceSelect.options[serviceSelect.selectedIndex];
            var price = option ? option.getAttribute("data-price") : "";
            if (price && !priceInput.value) {
                priceInput.value = Math.round(Number(price));
            }
        });
    }
});
