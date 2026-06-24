package com.graphhopper.navigation;

import com.graphhopper.GHRequest;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;

import static com.graphhopper.util.Parameters.CH.DISABLE;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class NavigateResourceTest {


    @Test
    public void voiceInstructionsTest() {

        List<Double> bearings = NavigateResource.getBearing("");
        assertEquals(0, bearings.size());
        assertEquals(Collections.EMPTY_LIST, bearings);

        bearings = NavigateResource.getBearing("100,1");
        assertEquals(1, bearings.size());
        assertEquals(100, bearings.get(0), .1);

        bearings = NavigateResource.getBearing(";100,1;;");
        assertEquals(4, bearings.size());
        assertEquals(100, bearings.get(1), .1);
    }

    @Test
    public void requestTypeUsesQueryParamWhenHintMissing() {
        GHRequest request = new GHRequest();

        assertEquals("mapbox", NavigateResource.getRequestType(request, "mapbox"));
    }

    @Test
    public void requestTypePrefersHintOverQueryParam() {
        GHRequest request = new GHRequest();
        request.putHint("type", "mapbox");

        assertEquals("mapbox", NavigateResource.getRequestType(request, "json"));
    }

    @Test
    public void headingDisablesCHForPostNavigationRequests() {
        GHRequest request = new GHRequest();
        request.setHeadings(List.of(90d));

        NavigateResource.prepareNavigationRequest(request);

        assertTrue(request.getHints().getBool(DISABLE, false));
    }

    @Test
    public void requestWithoutHeadingKeepsCHEnabled() {
        GHRequest request = new GHRequest();

        NavigateResource.prepareNavigationRequest(request);

        assertFalse(request.getHints().getBool(DISABLE, false));
    }

}
