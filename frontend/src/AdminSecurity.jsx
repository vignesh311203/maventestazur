import React,{useEffect,useState} from "react";

export default function AdminSecurity({api,onRecovery}){
 const [status,setStatus]=useState(null),[setup,setSetup]=useState(null),[password,setPassword]=useState(""),[code,setCode]=useState(""),[busy,setBusy]=useState(false),[notice,setNotice]=useState("");
 useEffect(()=>{let active=true;api("/api/admin/mfa/status").then(value=>{if(active)setStatus(value)}).catch(e=>{if(active)setNotice(e.message)});return()=>{active=false}},[]);
 async function submit(e){
  e.preventDefault();if(busy)return;setBusy(true);setNotice("");
  try{
   if(!status.enabled&&!setup){
    const result=await api("/api/admin/mfa/start",{method:"POST",body:JSON.stringify({current_password:password})});
    setSetup(result);setPassword("");setCode("");setNotice("Enter the setup key into your authenticator app within 10 minutes.");
   }else{
    const path=status.enabled?"/api/admin/mfa/recovery-codes":"/api/admin/mfa/confirm";
    const result=await api(path,{method:"POST",body:JSON.stringify({current_password:password,code})});
    setPassword("");setCode("");setSetup(null);onRecovery(result.recovery_codes);
   }
  }catch(err){setNotice(err.message)}finally{setBusy(false)}
 }
 return <section className="card password-panel">
  <h3>Administrator two-factor authentication</h3>
  <p className="muted">Use a time-based authenticator app. Enrolled accounts require a fresh code or an unused recovery code for sign-in.</p>
  {notice&&<p role="status">{notice}</p>}
  {status&&<><p>{status.enabled?"MFA enabled. "+status.recovery_codes_remaining+" recovery codes remaining.":status.required?"MFA enrollment is required before administrator operations.":"MFA is not yet enabled for this account."}</p>
  {setup&&<div className="notice"><strong>Authenticator setup key</strong><p><code style={{overflowWrap:"anywhere"}}>{setup.secret}</code></p><p>In your authenticator app, add a time-based account named {setup.issuer} ({setup.account}), then enter its six-digit code. Do not share this key.</p><button type="button" className="outline" onClick={()=>{setSetup(null);setPassword("");setCode("");setNotice("")}}>Cancel setup</button></div>}
  <form className="public-form" onSubmit={submit}>
   <label>MFA current password<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label>
   {(setup||status.enabled)&&<label>Fresh authenticator code<input type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength="6" required value={code} onChange={e=>setCode(e.target.value)}/></label>}
   <button className="primary" disabled={busy}>{busy?"Working…":status.enabled?"Replace recovery codes":setup?"Confirm MFA enrollment":"Set up MFA"}</button>
  </form></>}
 </section>
}
