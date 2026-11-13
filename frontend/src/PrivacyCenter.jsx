import React,{useEffect,useState} from "react";

const kindLabels={erasure:"Account erasure",correction:"Correction",withdraw_contact:"Withdraw contact consent"};
const statusLabels={pending:"Pending",in_review:"In review",resolved:"Resolved",declined:"Declined",erased:"Application data erased"};

export function CustomerPrivacyCenter({request}){
 const [rows,setRows]=useState([]),[kind,setKind]=useState("correction"),[password,setPassword]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
 async function refresh(){setRows(await request("/api/customer/privacy/requests"))}
 useEffect(()=>{refresh().catch(e=>setNotice(e.message))},[]);
 async function download(){
  setBusy(true);setNotice("");
  try{
   const data=await request("/api/customer/privacy/export");
   const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
   const a=document.createElement("a");a.href=url;a.download="metroestate-my-data.json";document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
   setNotice("Your private data export is ready. Store it securely.");
  }catch(e){setNotice(e.message)}finally{setBusy(false)}
 }
 async function submit(e){
  e.preventDefault();if(busy)return;setBusy(true);setNotice("");
  try{await request("/api/customer/privacy/requests",{kind,current_password:password});setPassword("");await refresh();setNotice(kind==="withdraw_contact"?"Contact permission withdrawn for your existing account enquiries.":"Privacy request recorded for administrator review.")}
  catch(e){setNotice(e.message)}finally{setBusy(false)}
 }
 return <section id="my-privacy" className="public-card">
  <h2>My privacy</h2><p>Access your account-owned records, withdraw contact permission or request correction or erasure.</p>
  <button type="button" className="outline" onClick={download} disabled={busy}>Download my data</button>
  <form className="public-form" onSubmit={submit}><label>Privacy request type<select value={kind} onChange={e=>setKind(e.target.value)}>{Object.entries(kindLabels).map(([k,label])=><option key={k} value={k}>{label}</option>)}</select></label>
   {kind==="erasure"&&<p className="notice">Erasure is reviewed by an administrator. Approved application erasure removes your account-owned enquiries and messages and prevents further sign-in. External or legally retained records need separate review.</p>}
   {kind==="correction"&&<p>After submitting, use your private conversation to identify the information that needs correction. Do not share sensitive documents.</p>}
   <label>Privacy request current password<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label>
   <button className="primary" disabled={busy}>Submit privacy request</button>
  </form>
  {notice&&<p role="status">{notice}</p>}
  <h3>My privacy requests</h3>{rows.length?<div className="table-wrap"><table><thead><tr><th>Request</th><th>Submitted</th><th>Status</th></tr></thead><tbody>{rows.map(x=><tr key={x.id}><td>{kindLabels[x.kind]}</td><td>{new Date(x.created_at).toLocaleDateString("en-IN")}</td><td>{statusLabels[x.status]||x.status}</td></tr>)}</tbody></table></div>:<p>No privacy requests recorded.</p>}
  <button type="button" className="outline" onClick={()=>refresh().catch(e=>setNotice(e.message))}>Refresh privacy requests</button>
 </section>
}

export function AdminPrivacyCenter({api}){
 const [rows,setRows]=useState([]),[selected,setSelected]=useState(null),[decision,setDecision]=useState("in_review"),[password,setPassword]=useState(""),[retention,setRetention]=useState(false),[external,setExternal]=useState(false),[confirmation,setConfirmation]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
 async function refresh(){setRows(await api("/api/admin/privacy/requests?status=all"))}
 useEffect(()=>{refresh().catch(e=>setNotice(e.message))},[]);
 const current=rows.find(x=>x.id===selected);
 function choose(id){setSelected(id);setDecision("in_review");setPassword("");setRetention(false);setExternal(false);setConfirmation("");setNotice("")}
 async function submit(e){
  e.preventDefault();if(busy||!current)return;
  setBusy(true);setNotice("");
  try{
   await api("/api/admin/privacy/requests/"+current.id,{method:"PATCH",body:JSON.stringify({decision,current_password:password,retention_reviewed:retention,external_copies_reviewed:external,erasure_confirmation:confirmation})});
   setPassword("");setConfirmation("");setSelected(null);await refresh();setNotice("Privacy request decision recorded.")
  }catch(e){setNotice(e.message)}finally{setBusy(false)}
 }
 return <section className="card"><div className="section-head"><h3>Customer privacy requests</h3><button className="outline" onClick={()=>refresh().catch(e=>setNotice(e.message))}>Refresh</button></div>
  <p className="muted">Review the customer's identity, scope, legal retention obligations and external copies before making an erasure decision. This workspace does not erase external provider data.</p>
  {notice&&<p role="status">{notice}</p>}
  {rows.length?<div className="table-wrap"><table><thead><tr><th>Customer</th><th>Request</th><th>Status</th><th>Action</th></tr></thead><tbody>{rows.map(x=><tr key={x.id}><td>{x.customer_name}</td><td>{kindLabels[x.kind]}</td><td>{statusLabels[x.status]||x.status}</td><td>{["pending","in_review"].includes(x.status)&&<button className="outline" onClick={()=>choose(x.id)}>Review request</button>}</td></tr>)}</tbody></table></div>:<p>No customer privacy requests.</p>}
  {current&&<form className="public-form" onSubmit={submit}><h4>Review request #{current.id}</h4><p>{current.customer_name} · {kindLabels[current.kind]}</p>
   <label>Decision<select value={decision} onChange={e=>{setDecision(e.target.value);setRetention(false);setExternal(false);setConfirmation("")}}><option value="in_review">Mark in review</option>{current.kind!=="erasure"&&<option value="resolved">Resolved manually</option>}<option value="declined">Decline request</option>{current.kind==="erasure"&&current.status==="in_review"&&<option value="erase_application">Erase account-owned application data</option>}</select></label>
   {decision==="erase_application"&&<><p className="notice">This removes account-owned leads, messages and review requests, invalidates sessions and deactivates the account. It retains a minimal audit reference. It cannot be undone in the application.</p>
    <label><input type="checkbox" required checked={retention} onChange={e=>setRetention(e.target.checked)}/> I reviewed legal retention obligations and the account is eligible for erasure.</label>
    <label><input type="checkbox" required checked={external} onChange={e=>setExternal(e.target.checked)}/> I reviewed external copies and third-party records separately.</label>
    <label>Type ERASE ACCOUNT to confirm<input required value={confirmation} onChange={e=>setConfirmation(e.target.value)} pattern="ERASE ACCOUNT"/></label></>}
   <label>Decision current password<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label>
   <button className="primary" disabled={busy||decision==="erase_application"&&(!retention||!external||confirmation!=="ERASE ACCOUNT")}>Record decision</button>
   <button type="button" className="outline" onClick={()=>choose(null)}>Cancel</button>
  </form>}
 </section>
}
