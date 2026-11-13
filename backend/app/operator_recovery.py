"""Interactive break-glass operation; run only in a trusted operator shell, never via HTTP."""
import argparse
import getpass
from sqlalchemy import select, delete
from . import main as m

def reset(email,new_password,reset_mfa=False):
    if not 16<=len(new_password)<=128:
        raise ValueError("Use a new password of 16 to 128 characters")
    with m.SessionLocal() as db:
        user=db.scalar(select(m.User).where(m.User.email==email.strip().lower(),m.User.role=="admin").with_for_update())
        if not user or not user.is_active:
            raise ValueError("Active administrator not found")
        user.password_hash=m.password_hash.hash(new_password)
        db.execute(delete(m.AuthSession).where(m.AuthSession.user_id==user.id))
        if reset_mfa:
            db.execute(delete(m.AdminMfa).where(m.AdminMfa.user_id==user.id))
        m.write_admin_audit(db,user,"operator.admin_recovery",str(user.id),None,{"password_reset":True,"mfa_reset":reset_mfa})
        db.commit()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email",required=True)
    parser.add_argument("--reset-mfa",action="store_true",help="Emergency only: remove lost MFA and require re-enrollment")
    args=parser.parse_args()
    if input("Type RECOVER ADMIN to confirm the operator-approved action: ")!="RECOVER ADMIN":
        raise SystemExit("Cancelled")
    password=getpass.getpass("New administrator password: ")
    if password!=getpass.getpass("Repeat new password: "):
        raise SystemExit("Passwords differ")
    reset(args.email,password,args.reset_mfa)
    print("Administrator password reset and sessions revoked. Re-enroll MFA if reset.")

if __name__=="__main__":
    main()
