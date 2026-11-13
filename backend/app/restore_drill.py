"""CI-only logical backup verification. Never use on a production database."""
import sys
from sqlalchemy import select,text
from . import main as m

def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ""
    expected={"seed":"metroestate_restore_source","verify":"metroestate_restore_target"}.get(mode)
    if not expected or m.engine.url.database!=expected or m.settings.app_env=="production":
        raise SystemExit("Refusing restore drill outside disposable test databases")
    with m.SessionLocal() as db:
        if mode=="seed":
            user=m.User(email="restore-fixture@example.com",full_name="Restore fixture",password_hash=m.password_hash.hash("DisposableFixturePass123!"),role="customer",privacy_notice_version=m.NOTICE_VERSION)
            plot=m.Plot(project="Restore fixture",plot_number="P1",area_sqft=1200,rate_per_sqft=3500)
            db.add_all([user,plot]);db.flush()
            db.add_all([
                m.PlotPhoto(plot_id=plot.id,jpeg=b"restore-photo-fixture"),
                m.Lead(owner_id=user.id,name="Restore fixture",phone="9999999999",interest="Restore enquiry",contact_consent_text=m.CONTACT_FORM_TEXT),
                m.SupportMessage(customer_id=user.id,sender="customer",client_id="restore-fixture-message",body="Restore fixture chat"),
                m.PrivacyRequest(customer_id=user.id,kind="correction"),
            ])
            db.commit()
            print("Disposable restore fixtures seeded")
        else:
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="0011_security_privacy"
            user=db.scalar(select(m.User).where(m.User.email=="restore-fixture@example.com"))
            assert user is not None and user.privacy_notice_version==m.NOTICE_VERSION
            plot=db.scalar(select(m.Plot).where(m.Plot.project=="Restore fixture"))
            assert plot is not None and plot.area_sqft==1200
            assert db.get(m.PlotPhoto,plot.id).jpeg==b"restore-photo-fixture"
            assert db.scalar(select(m.Lead).where(m.Lead.owner_id==user.id)).contact_consent_text==m.CONTACT_FORM_TEXT
            assert db.scalar(select(m.SupportMessage).where(m.SupportMessage.customer_id==user.id)).body=="Restore fixture chat"
            assert db.scalar(select(m.PrivacyRequest).where(m.PrivacyRequest.customer_id==user.id)).kind=="correction"
            print("Verified PostgreSQL restore: migration, account, plot, photograph, consent, message and privacy request")
if __name__=="__main__":
    main()
