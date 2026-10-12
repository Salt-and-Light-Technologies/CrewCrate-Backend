# Platform owner login

Owner email: `james.mcdougall@saltandlighttechnologies.com`.

The app does not store an owner password or grant ownership by matching an email.
Supabase verifies the login; the API checks the protected `public.cc_owners` table.
No live account or owner role was created by this documentation change.

## Set up the account securely

1. In the CrewCrate Supabase project's Authentication → Users, check whether this email
   already exists. If it does, use that account; reset its password if necessary.
2. If absent, use Add user/Create user, enter the email above, and choose a strong unique
   password directly in Supabase. Use the dashboard's confirmed-email setting only for
   this administrator account you control. Do not put the password in code or chat.
3. In this same project's SQL Editor, run the following after the account exists:

```sql
DO $$
DECLARE
    owner_user_id uuid;
BEGIN
    SELECT id INTO owner_user_id
    FROM auth.users
    WHERE lower(email) = lower('james.mcdougall@saltandlighttechnologies.com');

    IF owner_user_id IS NULL THEN
        RAISE EXCEPTION 'Create the Supabase authentication user first.';
    END IF;

    INSERT INTO public.cc_owners (user_id)
    VALUES (owner_user_id)
    ON CONFLICT (user_id) DO NOTHING;
END
$$;
```

4. In CrewCrate, enable Platform owner sign-in and use this email and the password set
   in Supabase. The API verifies ownership and opens Businesses/Requests/Account.

If the earlier database wipe removed this user/owner record, both steps are needed
again. The SQL grants platform-wide owner access to the specified existing account.
It does not create an authentication user or set/change any password.
