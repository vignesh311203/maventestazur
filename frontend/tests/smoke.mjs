import assert from "node:assert/strict";
import { chromium } from "playwright";
const web=process.env.TEST_WEB_URL||"http://localhost:5173";
const api=process.env.TEST_API_URL||"http://localhost:8000";
const adminEmail=process.env.TEST_ADMIN_EMAIL;
const adminPassword=process.env.TEST_ADMIN_PASSWORD;
assert(adminEmail&&adminPassword,"Use disposable test credentials");
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const errors=[];
page.on("pageerror",e=>errors.push(e.message));
const stamp=Date.now(),name="Mobile smoke "+stamp+" <b data-smoke=literal>text</b>";
const customerEmail="mobile-"+stamp+"@example.com",customerPassword="SmokeCustomerPass123!";
const auth=t=>({Authorization:"Bearer "+t});
async function post(path,data,headers){return page.request.post(api+path,{data,headers});}
async function signIn(email,password){await page.getByLabel("Email",{exact:true}).fill(email);await page.getByLabel("Password",{exact:true}).fill(password);await page.getByRole("button",{name:"Sign in",exact:true}).click();}
async function nav(label){await page.getByRole("navigation").getByRole("button",{name:label,exact:true}).click();}
async function noOverflow(){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),"Page overflows mobile viewport");}
try{
  const health=await page.request.get(api+"/health");assert.equal(health.status(),200);assert.equal((await health.json()).status,"ok");
  console.log("PASS backend health");
  await page.goto(web);await page.getByRole("heading",{name:"Find a plot that fits your future."}).waitFor();await noOverflow();
  await page.getByRole("heading",{name:"Explore available plots"}).waitFor();
  assert.equal(await page.getByRole("heading",{name:"Administrator sign in"}).count(),0);
  console.log("PASS public mobile homepage and separate login");
  await page.getByRole("heading",{name:"Home loan EMI"}).waitFor();await page.getByRole("heading",{name:"Plot cost estimator"}).waitFor();await page.getByRole("heading",{name:"Property growth projection"}).waitFor();
  console.log("PASS all public calculator views");
  await page.getByLabel("Full name",{exact:true}).fill(name);await page.getByLabel("Phone number",{exact:true}).fill("9999999999");await page.getByLabel("Property interest",{exact:true}).fill("Test residential plot");await page.getByRole("checkbox").check();
  const guest=page.waitForResponse(r=>r.url().endsWith("/api/public/enquiries")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"Send enquiry",exact:true}).click();assert.equal((await guest).status(),201);
  await page.getByRole("status").getByText(/Your enquiry has been received/).waitFor();
  console.log("PASS guest enquiry without email and with consent");
  await page.goto(web+"/admin");await page.getByRole("heading",{name:"Administrator sign in"}).waitFor();await signIn(adminEmail,adminPassword);
  await page.getByRole("heading",{name:"New enquiry notifications"}).waitFor();
  await page.getByRole("row").filter({hasText:name}).waitFor();assert.equal(await page.locator("b[data-smoke=literal]").count(),0);
  console.log("PASS separate admin sign in and notification inbox; no HTML injection");
  await nav("Leads");const row=page.getByRole("row").filter({hasText:name});await row.waitFor();
  console.log("PASS enquiry visible in admin lead tracker");
  const changed=page.waitForResponse(r=>r.url().includes("/api/leads/")&&r.request().method()==="PATCH");await row.getByRole("combobox").selectOption("contacted");assert.equal((await changed).status(),200);
  console.log("PASS change lead status to Contacted");
  await nav("Admin");const inbox=page.locator("section").filter({has:page.getByRole("heading",{name:"New enquiry notifications"})});await inbox.getByRole("button",{name:"Refresh",exact:true}).click();await inbox.getByRole("row").filter({hasText:name}).waitFor({state:"detached"});
  console.log("PASS contacted lead disappears after refresh");
  const adminLogin=await post("/api/auth/login",{email:adminEmail,password:adminPassword});assert.equal(adminLogin.status(),200);const adminHeaders=auth((await adminLogin.json()).access_token);
  const plot=await post("/api/plots",{project:"Smoke Enclave",plot_number:"P101",area_sqft:1200,rate_per_sqft:3500,x:20,y:30},adminHeaders);assert.equal(plot.status(),201);
  await nav("Layout");await page.getByRole("button",{name:/P101/}).click();await page.getByText("Smoke Enclave · P101",{exact:true}).waitFor();
  console.log("PASS interactive admin plot inventory");
  await page.getByRole("button",{name:"Sign out",exact:true}).click();
  await page.goto(web+"/customer");await page.getByRole("button",{name:"Create a customer account"}).click();await page.getByLabel("Full name",{exact:true}).fill("Smoke Customer");await page.getByLabel("Email",{exact:true}).fill(customerEmail);await page.getByLabel("Password",{exact:true}).fill(customerPassword);
  const registered=page.waitForResponse(r=>r.url().endsWith("/api/auth/register")&&r.request().method()==="POST");await page.getByRole("button",{name:"Create account",exact:true}).click();const reg=await registered;assert.equal(reg.status(),201);const customer=await reg.json();const customerHeaders=auth(customer.access_token);
  await page.getByRole("heading",{name:"Welcome, Smoke Customer"}).waitFor();await noOverflow();
  assert.equal(await page.getByText(name,{exact:true}).count(),0);
  for(const path of ["/api/leads","/api/admin/users","/api/admin/lead-notifications","/api/admin/referrals","/api/admin/audit"]){assert.equal((await page.request.get(api+path,{headers:customerHeaders})).status(),403);}
  assert.deepEqual(await (await page.request.get(api+"/api/customer/enquiries",{headers:customerHeaders})).json(),[]);
  console.log("PASS customer registration and admin-only data isolation");
  await page.getByLabel("Phone number",{exact:true}).fill("9999999999");await page.getByLabel("Property interest",{exact:true}).fill("My private plot enquiry");await page.getByRole("checkbox").check();
  const own=page.waitForResponse(r=>r.url().endsWith("/api/leads")&&r.request().method()==="POST");await page.getByRole("button",{name:"Send enquiry",exact:true}).click();assert.equal((await own).status(),201);await page.getByText("My private plot enquiry",{exact:true}).waitFor();
  console.log("PASS customer sees only own enquiry status");
  await page.goto(web+"/admin");await signIn(customerEmail,customerPassword);await page.getByRole("status").getByText(/cannot access this panel/).waitFor();assert.equal(await page.getByRole("heading",{name:"Registered users"}).count(),0);
  console.log("PASS customer denied admin panel");
  await signIn(adminEmail,adminPassword);await page.getByRole("heading",{name:"New enquiry notifications"}).waitFor();await nav("Reviews");await page.getByLabel("Customer account ID").fill(String(customer.user.id));await page.getByRole("checkbox",{name:/explicitly agreed/}).check();const review=page.waitForResponse(r=>r.url().endsWith("/api/reviews/requests")&&r.request().method()==="POST");await page.getByRole("button",{name:"Record review request"}).click();assert.equal((await review).status(),201);
  console.log("PASS manual review consent");
  const partnerEmail="partner-"+stamp+"@example.com",partnerPassword="SmokePartnerPass123!";const partner=await post("/api/admin/users",{email:partnerEmail,full_name:"Smoke Partner",password:partnerPassword,role:"partner"},adminHeaders);assert.equal(partner.status(),201);await page.getByRole("button",{name:"Sign out",exact:true}).click();
  await page.goto(web+"/partner");await signIn(partnerEmail,partnerPassword);await page.getByRole("heading",{name:"Partner portal"}).waitFor();await page.getByLabel("Referred customer").fill("Browser Referral");await page.getByLabel("Phone",{exact:true}).fill("9999999999");const referral=page.waitForResponse(r=>r.url().endsWith("/api/partners/referrals")&&r.request().method()==="POST");await page.getByRole("button",{name:"Submit referral"}).click();const rr=await referral;assert.equal(rr.status(),201);const refId=(await rr.json()).id;await page.getByRole("row").filter({hasText:"Referral #"+refId}).waitFor();
  assert.equal(await page.getByText("Browser Referral",{exact:true}).count(),0);
  assert.equal((await page.request.patch(api+"/api/admin/referrals/"+refId,{headers:adminHeaders,data:{status:"approved",reward_amount:1000}})).status(),200);
  await page.getByRole("button",{name:"Refresh referrals"}).click();await page.getByRole("row").filter({hasText:"Referral #"+refId}).getByText("approved",{exact:true}).waitFor();
  console.log("PASS partner referral approval and masked personal details");
  await page.goto(web+"/");await page.getByRole("heading",{name:"Smoke Enclave"}).waitFor();await page.getByRole("button",{name:"Enquire about this plot"}).click();assert.match(await page.getByLabel("Property interest").inputValue(),/P101/);
  console.log("PASS public live inventory and property enquiry selection");
  assert.equal((await page.request.get(api+"/api/whatsapp/webhook")).status(),404);
  assert.equal((await page.request.get(api+"/api/admin/whatsapp/status")).status(),404);
  console.log("PASS no active WhatsApp integration");
  if(web===api){assert.match((await page.request.get(web+"/admin")).headers()["x-robots-tag"],/noindex/);assert.match(await (await page.request.get(web+"/robots.txt")).text(),/Sitemap:/);assert.match(await (await page.request.get(web+"/sitemap.xml")).text(),/urlset/);assert.equal((await page.request.get(web+"/.env")).status(),404);console.log("PASS SEO routes and private no-index headers");}
  assert.deepEqual(errors,[],"Unexpected browser JavaScript errors");
  console.log("All public, customer, admin, privacy and mobile workflow checks passed.");
}finally{await browser.close();}
