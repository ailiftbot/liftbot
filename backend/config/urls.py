from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView  # <--- YEH IMPORT ADD KIYA HAI

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- Marketing Pages (Professional Setup) ---
    path('', TemplateView.as_view(template_name='marketing/home.html'), name='home'),
    path('about/', TemplateView.as_view(template_name='marketing/about.html'), name='about'),
    path('contact/', TemplateView.as_view(template_name='marketing/contact.html'), name='contact'),
    # ---------------------------------------------

    # --- Existing App Routes ---
    path('', include('apps.accounts.urls')),
    path('', include('apps.workspaces.urls')),
    path('employees/', include('apps.employees.urls')),
    path('knowledge/', include('apps.knowledge.urls')),
    path('chat/', include('apps.chat.urls')),
    path('leads/', include('apps.leads.urls')),
    path('billing/', include('apps.billing.urls')),
    path('api/widget/', include('apps.chat.widget_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)