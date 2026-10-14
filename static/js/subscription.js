(() => {
    const checkout = document.getElementById("paddleCheckout");
    if (!checkout) {
        return;
    }

    const buttons = checkout.querySelectorAll(".paddle-checkout-button");
    const disableCheckout = () => {
        buttons.forEach((button) => {
            button.disabled = true;
            button.title = "Paddle checkout trenutno nije dostupan.";
        });
    };
    if (!window.Paddle) {
        disableCheckout();
        return;
    }

    const config = checkout.dataset;
    const priceIds = {
        monthly: config.monthlyPriceId,
        yearly: config.yearlyPriceId,
    };

    try {
        if (config.environment === "sandbox") {
            window.Paddle.Environment.set("sandbox");
        }
        window.Paddle.Initialize({ token: config.clientToken });
    } catch (error) {
        console.error("Paddle checkout initialization failed.", error);
        disableCheckout();
        return;
    }

    buttons.forEach((button) => {
        button.addEventListener("click", () => {
            const plan = button.dataset.plan;
            const priceId = priceIds[plan];
            if (!priceId) {
                return;
            }

            window.Paddle.Checkout.open({
                items: [{ priceId, quantity: 1 }],
                customer: config.customerEmail ? { email: config.customerEmail } : undefined,
                customData: {
                    salon_id: config.salonId,
                    salon_binding: config.salonBinding,
                    plan,
                },
                settings: {
                    displayMode: "overlay",
                    theme: "dark",
                },
            });
        });
    });
})();
