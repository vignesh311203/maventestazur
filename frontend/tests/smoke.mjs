import assert from "node:assert/strict";
import { chromium } from "playwright";

const web = process.env.TEST_WEB_URL || "http://localhost:5173";
const api = process.env.TEST_API_URL || "http://localhost:8000";
const adminEmail = process.env.TEST_ADMIN_EMAIL;
const adminPassword = process.env.TEST_ADMIN_PASSWORD;
assert(adminEmail && adminPassword, "Provide dedicated test admin credentials. Never run against production.");
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const name = "Browser Smoke " + Date.now();
const customerEmail = "smoke-" + Date.now() + "@example.com";
const customerPassword = "SmokeCustomerPass123!";
async function signIn(email, password) {
  await page.getByPlaceholder("Email", { exact: true }).fill(email);
  await page.getByPlaceholder("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).waitFor();
}
async function navigate(tab) {
  await page.getByRole("navigation").getByRole("button", { name: tab, exact: true }).click();
}
try {
  const health = await page.request.get(api + "/health");
  assert.equal(health.status(), 200);
  assert.equal((await health.json()).status, "ok");
  console.log("PASS backend health");
  await page.goto(web);
  await page.getByText("MetroEstate", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Sign in", exact: true }).waitFor();
  console.log("PASS frontend opens");
  await signIn(adminEmail, adminPassword);
  await page.getByRole("navigation").getByRole("button", { name: "Admin", exact: true }).waitFor();
  console.log("PASS admin login");
  await navigate("Leads");
  await page.getByPlaceholder("Customer name").fill(name);
  await page.getByPlaceholder("Phone number").fill("9999999999");
  await page.getByPlaceholder("Property interest").fill("Test residential plot");
  // Deliberately leave optional email blank.
  const created = page.waitForResponse(r => r.url().endsWith("/api/leads") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Save lead" }).click();
  const response = await created;
  assert.equal(response.status(), 201, await response.text());
  console.log("PASS create new enquiry without email");
  await page.getByRole("row").filter({ hasText: name }).waitFor();
  console.log("PASS enquiry visible in lead tracker");
  await navigate("Admin");
  const inbox = page.locator("section").filter({ has: page.getByRole("heading", { name: "New enquiry notifications" }) });
  await inbox.getByRole("row").filter({ hasText: name }).waitFor();
  console.log("PASS enquiry visible in admin inbox");
  await navigate("Leads");
  const row = page.getByRole("row").filter({ hasText: name });
  await row.waitFor();
  const updated = page.waitForResponse(r => r.url().includes("/api/leads/") && r.request().method() === "PATCH");
  await row.getByRole("combobox").selectOption("contacted");
  assert.equal((await updated).status(), 200);
  console.log("PASS change lead status to Contacted");
  await navigate("Admin");
  const refreshed = page.waitForResponse(r => r.url().endsWith("/api/admin/lead-notifications") && r.request().method() === "GET");
  await inbox.getByRole("button", { name: "Refresh" }).click();
  const inboxResponse = await refreshed;
  assert.equal(inboxResponse.status(), 200);
  assert(!(await inboxResponse.json()).some(x => x.name === name));
  await inbox.getByRole("row").filter({ hasText: name }).waitFor({ state: "detached" });
  console.log("PASS contacted lead disappears after inbox refresh");
  const registered = await page.request.post(api + "/api/auth/register", { data: { email: customerEmail, full_name: "Smoke Customer", password: customerPassword } });
  assert.equal(registered.status(), 201, await registered.text());
  const customerToken = (await registered.json()).access_token;
  const denied = await page.request.get(api + "/api/admin/lead-notifications", { headers: { Authorization: "Bearer " + customerToken } });
  assert.equal(denied.status(), 403);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await signIn(customerEmail, customerPassword);
  assert.equal(await page.getByRole("navigation").getByRole("button", { name: "Admin", exact: true }).count(), 0);
  await navigate("Leads");
  await page.getByRole("heading", { name: "Lead tracker" }).waitFor();
  assert.equal(await page.getByRole("row").filter({ hasText: name }).count(), 0);
  console.log("PASS non-admin API denial and restricted dashboard");
  assert.equal((await page.request.get(api + "/api/whatsapp/webhook")).status(), 404);
  assert.equal((await page.request.get(api + "/api/admin/whatsapp/status")).status(), 404);
  assert.equal(await page.getByText(/WhatsApp Cloud API/i).count(), 0);
  console.log("PASS no active WhatsApp integration");
  assert.deepEqual(errors, [], "Unexpected browser JavaScript errors");
  console.log("All ten MetroEstate checklist checks passed.");
} finally {
  await browser.close();
}
